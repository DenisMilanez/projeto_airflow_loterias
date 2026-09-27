from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime

from loterias import caminhos
from loterias.config import listar_modalidades
from loterias.log import aviso, erro, evento, log, secao, set_context


def fmt_duration(sec: float) -> str:
    if sec < 60:
        return f"{sec:.1f}s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{int(m)}min{int(s):02d}s"
    h, m = divmod(m, 60)
    return f"{int(h)}h{int(m):02d}min"


async def run_subprocess(label: str, modulo: str, *args: str) -> tuple[str, int, float]:
    cmd = [sys.executable, "-m", modulo, *args]
    log(f"-> iniciando: {label}  ({' '.join(args)})")
    t0 = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(caminhos.raiz()))
    rc = await proc.wait()
    elapsed = time.perf_counter() - t0
    if rc == 0:
        evento("subprocess_ok", label=label, duracao=fmt_duration(elapsed))
    else:
        evento("subprocess_falhou", label=label, exit_code=rc, duracao=fmt_duration(elapsed))
    return (label, rc, elapsed)


async def run_subprocess_com_semaforo(
    sem: asyncio.Semaphore, label: str, modulo: str, *args: str
) -> tuple[str, int, float]:
    async with sem:
        return await run_subprocess(label, modulo, *args)


async def fase_setup_banco() -> int:
    secao("SETUP DO BANCO (idempotente)")
    for script in ["loterias.schema.publico", "loterias.schema.observabilidade"]:
        _, rc, _ = await run_subprocess(f"setup:{script}", script)
        if rc != 0:
            erro(f"{script} falhou - abortando")
            return rc
    return 0


async def fase_bronze_concursos(modalidades: list[str], paralelismo: int) -> dict[str, int]:
    secao(
        f"BRONZE CONCURSOS - {len(modalidades)} modalidades em paralelo (paralelismo={paralelismo})"
    )
    sem = asyncio.Semaphore(paralelismo)
    tasks = [
        run_subprocess_com_semaforo(
            sem,
            f"bronze.concursos[{m}]",
            "loterias.bronze.concursos",
            "--modalidade",
            m,
        )
        for m in modalidades
    ]
    results = await asyncio.gather(*tasks)
    return {label.split("[")[1].rstrip("]"): rc for label, rc, _ in results}


async def fase_silver_concursos(modalidades: list[str]) -> dict[str, int]:
    secao(f"SILVER CONCURSOS - sequencial entre {len(modalidades)} modalidades")
    out: dict[str, int] = {}
    for m in modalidades:
        _, rc, _ = await run_subprocess(
            f"silver.concursos[{m}]",
            "loterias.silver.concursos",
            "--modalidade",
            m,
        )
        out[m] = rc
    return out


async def fase_gold_concursos(modalidades: list[str]) -> dict[str, int]:
    secao(f"GOLD CONCURSOS - sequencial entre {len(modalidades)} modalidades")
    out: dict[str, int] = {}
    for m in modalidades:
        _, rc, _ = await run_subprocess(
            f"gold.concursos[{m}]",
            "loterias.gold.concursos",
            "--modalidade",
            m,
        )
        out[m] = rc
    return out


async def fase_bronze_locais(modalidades: list[str]) -> dict[str, int]:
    secao(f"BRONZE LOCAIS_SORTE - sequencial entre {len(modalidades)} modalidades")
    aviso("modo conservador: nunca paralelizar locais_sorte (servicebus3 banha facil)")
    out: dict[str, int] = {}
    for m in modalidades:
        _, rc, _ = await run_subprocess(
            f"bronze.locais[{m}]",
            "loterias.bronze.locais_sorte",
            "--modalidade",
            m,
        )
        out[m] = rc
    return out


async def fase_silver_locais(modalidades: list[str]) -> dict[str, int]:
    secao(f"SILVER LOCAIS_SORTE - sequencial entre {len(modalidades)} modalidades")
    out: dict[str, int] = {}
    for m in modalidades:
        _, rc, _ = await run_subprocess(
            f"silver.locais[{m}]",
            "loterias.silver.locais_sorte",
            "--modalidade",
            m,
        )
        out[m] = rc
    return out


async def fase_gold_locais(modalidades: list[str]) -> dict[str, int]:
    secao(f"GOLD LOCAIS_SORTE - sequencial entre {len(modalidades)} modalidades")
    out: dict[str, int] = {}
    for m in modalidades:
        _, rc, _ = await run_subprocess(
            f"gold.locais[{m}]",
            "loterias.gold.locais_sorte",
            "--modalidade",
            m,
        )
        out[m] = rc
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Orquestrador multi-modalidade.")
    p.add_argument(
        "--modalidades",
        nargs="+",
        default=None,
        help="Modalidades a processar (default: TODAS do YAML)",
    )
    p.add_argument(
        "--com-locais-sorte",
        action="store_true",
        help="Inclui as 3 etapas de locais da sorte (default: só concursos)",
    )
    p.add_argument(
        "--sem-setup",
        action="store_true",
        help="Pula create_loterias_tables + setup_pipeline (use se ja rodou)",
    )
    p.add_argument(
        "--paralelismo-bronze-concursos",
        type=int,
        default=0,
        help="Quantos jogos coletam bronze concursos em paralelo. "
        "Default 0 = todos (=qtd modalidades). Use 1 pra serializar.",
    )
    return p.parse_args()


def resumir(titulo: str, resultados: dict[str, int]) -> int:
    erros = sum(1 for rc in resultados.values() if rc != 0)
    if erros == 0:
        log(f"  {titulo}: OK em todas as {len(resultados)} modalidades")
    else:
        log(f"  {titulo}: {erros} falha(s) - {[m for m, rc in resultados.items() if rc != 0]}")
    return erros


async def main_async() -> int:
    args = parse_args()
    set_context("TODAS", "orquestrador")

    modalidades = args.modalidades or listar_modalidades()
    paralelismo = args.paralelismo_bronze_concursos or len(modalidades)

    t_total = time.perf_counter()
    secao(f"PIPELINE MULTI-MODALIDADE - inicio {datetime.now().isoformat(timespec='seconds')}")
    log(f"modalidades: {', '.join(modalidades)}")
    log(f"com_locais_sorte: {args.com_locais_sorte}")
    log(f"setup_banco: {not args.sem_setup}")
    log(f"paralelismo_bronze_concursos: {paralelismo}")

    resumos: dict[str, dict[str, int]] = {}

    if not args.sem_setup:
        rc = await fase_setup_banco()
        if rc != 0:
            return rc

    resumos["bronze_concursos"] = await fase_bronze_concursos(modalidades, paralelismo)

    resumos["silver_concursos"] = await fase_silver_concursos(modalidades)

    resumos["gold_concursos"] = await fase_gold_concursos(modalidades)

    if args.com_locais_sorte:
        resumos["bronze_locais"] = await fase_bronze_locais(modalidades)
        resumos["silver_locais"] = await fase_silver_locais(modalidades)
        resumos["gold_locais"] = await fase_gold_locais(modalidades)

    secao("RESUMO GLOBAL")
    total_erros = 0
    for fase, res in resumos.items():
        total_erros += resumir(fase, res)

    evento(
        "fim",
        modalidades=len(modalidades),
        fases=len(resumos),
        total_falhas=total_erros,
        duracao_total=fmt_duration(time.perf_counter() - t_total),
    )

    if total_erros > 0:
        aviso(
            f"{total_erros} falha(s) durante a execucao - checar logs acima e "
            f"pipeline.execucao_arquivo / pipeline.concurso_fila / pipeline.locais_sorte_fila"
        )
    else:
        log("todas as fases concluiram sem erros")

    return 1 if total_erros > 0 else 0


def main() -> int:
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        erro("interrompido pelo usuario (Ctrl+C)")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
