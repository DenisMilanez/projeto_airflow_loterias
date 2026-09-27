from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from loterias import caminhos
from loterias.db import devolver, emprestar
from loterias.log import evento, log, secao, set_context

DATA_DIRS = [
    caminhos.bronze(),
    caminhos.silver(),
    caminhos.gold(),
]


def run_step(modulo: str, extra_args: list[str] | None = None) -> tuple[float, int]:
    cmd = [sys.executable, "-m", modulo]
    if extra_args:
        cmd += extra_args
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=str(caminhos.raiz()))
    elapsed = time.perf_counter() - t0
    return elapsed, r.returncode


def fmt_duration(sec: float) -> str:
    if sec < 60:
        return f"{sec:.1f}s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{int(m)}min{int(s):02d}s"
    h, m = divmod(m, 60)
    return f"{int(h)}h{int(m):02d}min"


def dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.2f} KB"
    if n < 1024**3:
        return f"{n / 1024**2:.2f} MB"
    return f"{n / 1024**3:.2f} GB"


def wipe_all_tables() -> None:
    conn = emprestar()
    cur = conn.cursor()
    try:
        cur.execute("DROP SCHEMA IF EXISTS pipeline CASCADE")
        for table in (
            "ganhador_loterica",
            "loterica",
            "ganhador_municipio",
            "rateio",
            "dezena",
            "concurso",
            "local_sorteio",
            "faixa",
            "localidade",
            "tipo_jogo",
        ):
            cur.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
        conn.commit()
    finally:
        cur.close()
        devolver(conn)


def collect_metrics() -> dict:

    metrics: dict = {}
    metrics["bronze_bytes"] = dir_size_bytes(caminhos.bronze())
    metrics["silver_bytes"] = dir_size_bytes(caminhos.silver())
    metrics["gold_bytes"] = dir_size_bytes(caminhos.gold())
    metrics["disk_total_bytes"] = (
        metrics["bronze_bytes"] + metrics["silver_bytes"] + metrics["gold_bytes"]
    )

    conn = emprestar()
    cur = conn.cursor()
    try:
        cur.execute("SELECT pg_database_size(current_database())")
        metrics["db_bytes"] = int(cur.fetchone()[0])

        counts = {}
        for schema, table in [
            ("public", "tipo_jogo"),
            ("public", "faixa"),
            ("public", "localidade"),
            ("public", "local_sorteio"),
            ("public", "concurso"),
            ("public", "dezena"),
            ("public", "rateio"),
            ("public", "ganhador_municipio"),
            ("public", "loterica"),
            ("public", "ganhador_loterica"),
            ("pipeline", "execucao"),
            ("pipeline", "execucao_arquivo"),
            ("pipeline", "concurso_fila"),
            ("pipeline", "locais_sorte_fila"),
            ("pipeline", "saude_diaria"),
        ]:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
                counts[f"{schema}.{table}"] = cur.fetchone()[0]
            except Exception:
                counts[f"{schema}.{table}"] = None
        metrics["row_counts"] = counts
    finally:
        cur.close()
        devolver(conn)

    metrics["bronze_arquivos"] = len(list((caminhos.bronze()).rglob("*.parquet")))
    metrics["silver_pastas"] = len(list((caminhos.silver()).rglob("concurso.parquet")))
    return metrics


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Orquestrador end-to-end multi-modo.")
    p.add_argument(
        "--modo",
        choices=["wipe", "incremental"],
        default="wipe",
        help="wipe (default): zera tudo e re-cria. incremental: só roda os passos.",
    )
    p.add_argument("--modalidade", default="MEGA_SENA", help="Modalidade (default: MEGA_SENA)")
    p.add_argument(
        "--com-locais-sorte",
        action="store_true",
        help="Inclui passos de locais da sorte (default: só concursos)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    modalidade = args.modalidade
    set_context(modalidade, "orquestrador")
    t_total = time.perf_counter()
    timings: dict[str, float] = {}

    secao(
        f"PIPELINE COMPLETO - modo={args.modo} modalidade={modalidade} "
        f"locais_sorte={args.com_locais_sorte}"
    )
    log(f"inicio {datetime.now().isoformat(timespec='seconds')}")

    if args.modo == "wipe":
        secao("1. Limpeza de arquivos (bronze, silver, gold)")
        t0 = time.perf_counter()
        for d in DATA_DIRS:
            if d.exists():
                shutil.rmtree(d)
        for d in DATA_DIRS:
            d.mkdir(parents=True, exist_ok=True)
            (d / ".gitkeep").touch(exist_ok=True)
        timings["limpeza_arquivos"] = time.perf_counter() - t0
        evento("limpeza_arquivos_ok", duracao=fmt_duration(timings["limpeza_arquivos"]))

        secao("2. Limpeza do banco (DROP)")
        t0 = time.perf_counter()
        wipe_all_tables()
        timings["limpeza_bd"] = time.perf_counter() - t0
        evento("limpeza_bd_ok", duracao=fmt_duration(timings["limpeza_bd"]))

    for key, script in [
        ("setup_schema", "loterias.schema.publico"),
        ("setup_pipeline", "loterias.schema.observabilidade"),
    ]:
        secao(f"setup → {script}")
        elapsed, code = run_step(script)
        timings[key] = elapsed
        evento(
            "setup_step_ok" if code == 0 else "setup_step_falhou",
            script=script,
            duracao=fmt_duration(elapsed),
            exit_code=code,
        )
        if code != 0:
            return 1

    pipeline_steps = [
        ("bronze_concursos", "loterias.bronze.concursos", ["--modalidade", modalidade]),
        ("silver_concursos", "loterias.silver.concursos", ["--modalidade", modalidade]),
        ("gold_concursos", "loterias.gold.concursos", ["--modalidade", modalidade]),
    ]
    if args.com_locais_sorte:
        pipeline_steps += [
            ("bronze_locais", "loterias.bronze.locais_sorte", ["--modalidade", modalidade]),
            ("silver_locais", "loterias.silver.locais_sorte", ["--modalidade", modalidade]),
            ("gold_locais", "loterias.gold.locais_sorte", ["--modalidade", modalidade]),
        ]

    for key, script, extra in pipeline_steps:
        secao(f"etapa → {script} {' '.join(extra)}")
        elapsed, code = run_step(script, extra)
        timings[key] = elapsed
        if code == 0:
            evento("etapa_ok", script=script, duracao=fmt_duration(elapsed))
        else:
            evento(
                "etapa_falhou",
                script=script,
                duracao=fmt_duration(elapsed),
                exit_code=code,
                acao="seguindo defensivo",
            )

    timings["total"] = time.perf_counter() - t_total
    secao("RELATORIO")
    log(f"duracao total: {fmt_duration(timings['total'])}")
    metrics = collect_metrics()
    evento("contagem_db", **{k: v for k, v in metrics["row_counts"].items() if v is not None})

    print("\n" + "=" * 60)
    print("RELATORIO_PIPELINE_JSON_START")
    report = {
        "modo": args.modo,
        "modalidade": modalidade,
        "com_locais_sorte": args.com_locais_sorte,
        "timings_sec": timings,
        "metrics": metrics,
    }
    print(json.dumps(report, default=str, ensure_ascii=False))
    print("RELATORIO_PIPELINE_JSON_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
