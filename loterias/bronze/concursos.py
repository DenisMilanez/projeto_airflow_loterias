from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import aiohttp
import pandas as pd
from psycopg2.extras import execute_batch

from loterias import caminhos
from loterias.config import (
    codigo_api,
    concurso_esperado_hoje,
    concursos_param,
    deveria_ter_sorteio_hoje,
    fila_max_tentativas,
    fila_proximo_retry,
    id_tipo_jogo,
)
from loterias.db import conexao
from loterias.http import cabecalhos
from loterias.log import aviso, evento, log, secao, set_context

ENV_OVERRIDE_MAX_CONCURSO = "LOTERIAS_OVERRIDE_MAX_CONCURSO"


def db_max_numero_concurso(id_tj: int) -> int | None:
    raw = os.environ.get(ENV_OVERRIDE_MAX_CONCURSO)
    if raw is not None and str(raw).strip() != "":
        try:
            v = int(str(raw).strip())
        except ValueError as exc:
            raise ValueError(
                f"{ENV_OVERRIDE_MAX_CONCURSO} deve ser inteiro, recebido: {raw!r}"
            ) from exc
        if v < 0:
            raise ValueError(f"{ENV_OVERRIDE_MAX_CONCURSO} deve ser >= 0, recebido: {v}")
        log(f"override {ENV_OVERRIDE_MAX_CONCURSO}={v} - ignorando SELECT")
        return v

    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(numero_concurso) FROM public.concurso WHERE id_tipo_jogo = %s",
                (id_tj,),
            )
            row = cur.fetchone()
            return None if row is None or row[0] is None else int(row[0])


def db_data_proximo_concurso(id_tj: int) -> date | None:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT data_proximo_concurso FROM public.concurso
                WHERE id_tipo_jogo = %s
                ORDER BY numero_concurso DESC
                LIMIT 1
                """,
                (id_tj,),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else None


def fila_populate(modalidade: str, numeros: list[int]) -> None:
    if not numeros:
        return
    max_tent = fila_max_tentativas()
    with conexao() as conn:
        with conn.cursor() as cur:
            execute_batch(
                cur,
                """
                INSERT INTO pipeline.concurso_fila (modalidade, numero_concurso, max_tentativas)
                VALUES (%s, %s, %s)
                ON CONFLICT (modalidade, numero_concurso) DO NOTHING
                """,
                [(modalidade, n, max_tent) for n in numeros],
            )
        conn.commit()


def fila_get_retryaveis(modalidade: str) -> list[int]:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT numero_concurso
                FROM pipeline.concurso_fila
                WHERE modalidade = %s
                  AND status = 'erro'
                  AND tentativas < max_tentativas
                  AND (proximo_retry_em IS NULL OR proximo_retry_em <= NOW())
                ORDER BY numero_concurso
                """,
                (modalidade,),
            )
            return [r[0] for r in cur.fetchall()]


def fila_update_batch(
    modalidade: str,
    por_numero: dict[int, dict],
    id_execucao: int,
) -> None:
    if not por_numero:
        return
    now = datetime.now(UTC)

    sucessos: list[int] = []
    ausentes: list[int] = []
    transitorios: list[tuple[int, str]] = []
    for numero, resultado in por_numero.items():
        if is_sucesso(resultado):
            sucessos.append(numero)
        elif is_ausente(resultado):
            ausentes.append(numero)
        else:
            detalhe = resultado.get("exc") or f"HTTP {resultado.get('http_status', '?')}"
            transitorios.append((numero, detalhe))

    log(
        f"  atualizando fila: sucessos={len(sucessos)} ausentes={len(ausentes)} transitorios={len(transitorios)}"
    )

    with conexao() as conn:
        with conn.cursor() as cur:
            if sucessos:
                t0 = datetime.now(UTC)
                cur.execute(
                    """
                    UPDATE pipeline.concurso_fila
                    SET status='concluido',
                        tentativas=tentativas+1,
                        ultima_tentativa_em=%s,
                        proximo_retry_em=NULL,
                        erro_detalhe=NULL,
                        id_execucao=%s,
                        atualizado_em=%s
                    WHERE modalidade=%s AND numero_concurso = ANY(%s)
                    """,
                    (now, id_execucao, now, modalidade, sucessos),
                )
                dt = (datetime.now(UTC) - t0).total_seconds()
                log(f"  [OK] sucessos atualizados: {cur.rowcount} linhas em {dt:.2f}s")

            if ausentes:
                t0 = datetime.now(UTC)
                cur.execute(
                    """
                    SELECT numero_concurso, tentativas
                    FROM pipeline.concurso_fila
                    WHERE modalidade=%s AND numero_concurso = ANY(%s)
                    """,
                    (modalidade, ausentes),
                )
                tents_atuais = {n: t for n, t in cur.fetchall()}

                updates = []
                for n in ausentes:
                    t_nova = tents_atuais.get(n, 0) + 1
                    proximo = fila_proximo_retry(t_nova, now)
                    updates.append((t_nova, now, proximo, id_execucao, now, modalidade, n))

                execute_batch(
                    cur,
                    """
                    UPDATE pipeline.concurso_fila
                    SET status='erro',
                        tentativas=%s,
                        ultima_tentativa_em=%s,
                        proximo_retry_em=%s,
                        erro_detalhe='HTTP 404 (concurso pode ainda não ter sido publicado)',
                        id_execucao=%s,
                        atualizado_em=%s
                    WHERE modalidade=%s AND numero_concurso=%s
                    """,
                    updates,
                    page_size=200,
                )
                dt = (datetime.now(UTC) - t0).total_seconds()
                log(f"  [OK] 404 atualizados: {len(updates)} linhas em {dt:.2f}s")

            if transitorios:
                t0 = datetime.now(UTC)
                nums = [t[0] for t in transitorios]
                cur.execute(
                    """
                    SELECT numero_concurso, tentativas, max_tentativas
                    FROM pipeline.concurso_fila
                    WHERE modalidade=%s AND numero_concurso = ANY(%s)
                    """,
                    (modalidade, nums),
                )
                tents_atuais = {n: (t, m) for n, t, m in cur.fetchall()}

                updates = []
                for n, detalhe in transitorios:
                    t_atual, max_tent = tents_atuais.get(n, (0, fila_max_tentativas()))
                    t_nova = t_atual + 1
                    if t_nova >= max_tent:
                        novo_status = "abandonado"
                        proximo = None
                    else:
                        novo_status = "erro"
                        proximo = fila_proximo_retry(t_nova, now)
                    updates.append(
                        (
                            novo_status,
                            t_nova,
                            now,
                            proximo,
                            detalhe,
                            id_execucao,
                            now,
                            modalidade,
                            n,
                        )
                    )

                execute_batch(
                    cur,
                    """
                    UPDATE pipeline.concurso_fila
                    SET status=%s,
                        tentativas=%s,
                        ultima_tentativa_em=%s,
                        proximo_retry_em=%s,
                        erro_detalhe=%s,
                        id_execucao=%s,
                        atualizado_em=%s
                    WHERE modalidade=%s AND numero_concurso=%s
                    """,
                    updates,
                    page_size=200,
                )
                dt = (datetime.now(UTC) - t0).total_seconds()
                log(f"  [OK] transitorios atualizados: {len(updates)} linhas em {dt:.2f}s")

        conn.commit()


def db_insert_execucao_e_arquivos(
    modalidade: str,
    *,
    tipo: str,
    concurso_inicio: int | None,
    concurso_fim: int | None,
    total_coletados: int,
    total_erros: int,
    total_sem_dados: int,
    concurso_esperado: int | None,
    status: str,
    iniciado_em: datetime,
    finalizado_em: datetime,
    arquivos: list[tuple[Path, int, int, int]],
) -> int:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pipeline.execucao (
                    modalidade, tipo, concurso_inicio, concurso_fim,
                    total_coletados, total_erros, total_sem_dados, concurso_esperado,
                    status, iniciado_em, finalizado_em, fonte
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id_execucao
                """,
                (
                    modalidade,
                    tipo,
                    concurso_inicio,
                    concurso_fim,
                    total_coletados,
                    total_erros,
                    total_sem_dados,
                    concurso_esperado,
                    status,
                    iniciado_em,
                    finalizado_em,
                    "concursos",
                ),
            )
            id_exec = int(cur.fetchone()[0])
            if arquivos:
                execute_batch(
                    cur,
                    """
                    INSERT INTO pipeline.execucao_arquivo (
                        id_execucao, caminho_arquivo, concurso_inicio,
                        concurso_fim, total_registros, fonte
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (id_exec, caminhos.registrar(p), ci, cf, total, "concursos")
                        for p, ci, cf, total in arquivos
                    ],
                    page_size=100,
                )
        conn.commit()
        return id_exec


DEFAULT_HEADERS = cabecalhos()


def is_synthetic(d: Any) -> bool:
    return isinstance(d, dict) and d.get("status") in ("ausente", "erro_transitorio")


def is_ausente(d: Any) -> bool:
    return isinstance(d, dict) and d.get("status") == "ausente"


def is_transiente(d: Any) -> bool:
    return isinstance(d, dict) and d.get("status") == "erro_transitorio"


def is_sucesso(d: Any) -> bool:
    return not is_synthetic(d)


def url_base(modalidade: str) -> str:
    return f"https://servicebus2.caixa.gov.br/portaldeloterias/api/{codigo_api(modalidade)}/"


async def fetch_concurso(session: aiohttp.ClientSession, base: str, numero: int) -> dict:
    url = f"{base}{numero}"
    try:
        async with session.get(url, headers=DEFAULT_HEADERS) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
            if resp.status == 404:
                return {"status": "ausente", "numero": numero}
            return {"status": "erro_transitorio", "numero": numero, "http_status": resp.status}
    except Exception as exc:
        return {"status": "erro_transitorio", "numero": numero, "exc": str(exc)[:200]}


async def fetch_max_numero(session: aiohttp.ClientSession, base: str) -> int:
    async with session.get(base, headers=DEFAULT_HEADERS) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"GET raiz falhou: HTTP {resp.status} {text[:200]}")
        data = await resp.json(content_type=None)
    if not isinstance(data, dict) or "numero" not in data:
        raise RuntimeError(f"JSON raiz sem campo 'numero': {str(data)[:300]}")
    return int(data["numero"])


async def gather_com_semaforo(
    session: aiohttp.ClientSession, base: str, numeros: list[int], limite: int
) -> list[dict]:
    sem = asyncio.Semaphore(limite)

    async def um(n: int) -> dict:
        async with sem:
            return await fetch_concurso(session, base, n)

    return await asyncio.gather(*[um(n) for n in numeros])


def bronze_out_dir(modalidade: str) -> Path:
    jogo = codigo_api(modalidade)
    today = date.today()
    return (
        caminhos.bronze()
        / jogo
        / "concursos"
        / f"{today.year:04d}"
        / f"{today.month:02d}"
        / f"{today.day:02d}"
    )


def flush_parquet_batch(
    modalidade: str,
    out_dir: Path,
    batch: list[tuple[int, dict]],
    arquivos_gerados: list[tuple[Path, int, int, int]],
) -> None:
    if not batch:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    batch_sorted = sorted(batch, key=lambda x: x[0])
    n0, n1 = batch_sorted[0][0], batch_sorted[-1][0]
    today_str = date.today().strftime("%Y%m%d")
    jogo = codigo_api(modalidade)
    path = out_dir / f"{jogo}_concursos_{n0:04d}_{n1:04d}_{today_str}.parquet"
    df = pd.DataFrame(
        [{"numero": n, "payload": json.dumps(raw, ensure_ascii=False)} for n, raw in batch_sorted]
    )
    df.to_parquet(path, engine="pyarrow", index=False)
    arquivos_gerados.append((path, n0, n1, len(batch_sorted)))
    evento("parquet_salvo", arquivo=path.name, concursos=len(batch_sorted), intervalo=f"{n0}-{n1}")


async def _coletar_em_lotes(
    session,
    base,
    numeros,
    semaforo,
    chunk_size,
    label,
):
    por_numero: dict[int, dict] = {}
    total = len(numeros)
    for i in range(0, total, chunk_size):
        chunk = numeros[i : i + chunk_size]
        resultados = await gather_com_semaforo(session, base, chunk, semaforo)
        ok = err = aus = 0
        for n, r in zip(chunk, resultados, strict=True):
            por_numero[n] = r
            if is_sucesso(r):
                ok += 1
            elif is_ausente(r):
                aus += 1
            else:
                err += 1
        progresso = min(i + len(chunk), total)
        evento(
            f"chunk_{label}",
            progresso=f"{progresso}/{total}",
            ok=ok,
            ausentes=aus,
            erros=err,
            concursos=f"{chunk[0]}-{chunk[-1]}",
        )
    return por_numero


async def coleta_historico(modalidade: str, session) -> dict:
    base = url_base(modalidade)
    chunk_http = int(concursos_param(modalidade, "historico_chunk_http", 200))
    semaforo = int(concursos_param(modalidade, "historico_semaforo", 15))
    batch_parquet = int(concursos_param(modalidade, "batch_parquet", 500))

    secao("HISTORICO")
    log("consultando ultimo concurso na API (GET raiz)...")
    max_n = await fetch_max_numero(session, base)
    numeros = list(range(1, max_n + 1))
    evento("historico_inicio", ultimo_api=max_n, total_a_coletar=len(numeros))

    log("populando pipeline.concurso_fila...")
    await asyncio.to_thread(fila_populate, modalidade, numeros)

    log(f"1a passagem - semaforo={semaforo} chunks={chunk_http}")
    por_numero = await _coletar_em_lotes(session, base, numeros, semaforo, chunk_http, "historico")

    trans = [n for n, r in por_numero.items() if is_transiente(r)]
    if trans:
        log(f"retry de {len(trans)} transitorios - semaforo=5")
        retry = await _coletar_em_lotes(session, base, trans, 5, chunk_http, "retry")
        por_numero.update(retry)

    sucessos = sorted([(n, r) for n, r in por_numero.items() if is_sucesso(r)], key=lambda x: x[0])
    log(f"agrupando {len(sucessos)} sucessos em Parquet (lotes de {batch_parquet})...")
    out_dir = bronze_out_dir(modalidade)
    arquivos: list[tuple[Path, int, int, int]] = []
    buffer: list[tuple[int, dict]] = []
    for item in sucessos:
        buffer.append(item)
        if len(buffer) >= batch_parquet:
            flush_parquet_batch(modalidade, out_dir, buffer, arquivos)
            buffer.clear()
    flush_parquet_batch(modalidade, out_dir, buffer, arquivos)

    trans_finais = [n for n, r in por_numero.items() if is_transiente(r)]
    return {
        "modo": "historico",
        "total_esperado": len(numeros),
        "por_numero": por_numero,
        "sucessos": len(sucessos),
        "ausentes": sum(1 for r in por_numero.values() if is_ausente(r)),
        "trans_restantes": len(trans_finais),
        "arquivos": arquivos,
        "concurso_inicio": sucessos[0][0] if sucessos else None,
        "concurso_fim": sucessos[-1][0] if sucessos else None,
    }


async def coleta_incremental(modalidade: str, session, inicio: int) -> dict:
    base = url_base(modalidade)
    janela = int(concursos_param(modalidade, "incremental_janela", 5))
    stop_consecutivos = int(concursos_param(modalidade, "incremental_stop_consecutivos", 4))
    semaforo = int(concursos_param(modalidade, "incremental_semaforo", 3))
    batch_parquet = int(concursos_param(modalidade, "batch_parquet", 500))

    out_dir = bronze_out_dir(modalidade)
    arquivos: list[tuple[Path, int, int, int]] = []
    buffer: list[tuple[int, dict]] = []
    por_numero: dict[int, dict] = {}

    secao("INCREMENTAL")
    evento(
        "incremental_inicio",
        a_partir=inicio,
        janela=janela,
        semaforo=semaforo,
        stop_consecutivos=stop_consecutivos,
    )
    consecutive = 0
    n = inicio
    while True:
        jan = list(range(n, n + janela))
        resultados = await gather_com_semaforo(session, base, jan, semaforo)
        for num, r in zip(jan, resultados, strict=True):
            por_numero[num] = r
            if is_sucesso(r):
                consecutive = 0
                buffer.append((num, r))
                log(f"  [OK] concurso {num} coletado")
                if len(buffer) >= batch_parquet:
                    flush_parquet_batch(modalidade, out_dir, buffer, arquivos)
                    buffer.clear()
            else:
                consecutive += 1
                if is_ausente(r):
                    tag = "404 (nao existe)"
                else:
                    http = r.get("http_status")
                    exc = r.get("exc")
                    if http:
                        tag = f"HTTP {http}"
                        if http >= 500:
                            tag += " (sorteio futuro? API responde 500 nesses casos)"
                    elif exc:
                        tag = f"erro de rede: {str(exc)[:60]}"
                    else:
                        tag = "transitorio"
                log(
                    f"  [..] concurso {num}: {tag} (consecutivos={consecutive}/{stop_consecutivos})"
                )
                if consecutive >= stop_consecutivos:
                    break
        if consecutive >= stop_consecutivos:
            aviso(
                f"parando: {stop_consecutivos} erros consecutivos a partir de {n} "
                f"(provavelmente concursos ainda nao publicados pela Caixa - "
                f"vai retentar no proximo run)"
            )
            break
        n += janela

    if por_numero:
        await asyncio.to_thread(fila_populate, modalidade, list(por_numero.keys()))

    retryaveis = await asyncio.to_thread(fila_get_retryaveis, modalidade)
    retryaveis_fora = [x for x in retryaveis if x not in por_numero]
    if retryaveis_fora:
        log(f"Coletando {len(retryaveis_fora)} concursos retentaveis da fila...")
        retry_result = await _coletar_em_lotes(session, base, retryaveis_fora, 5, 200, "retry-fila")
        por_numero.update(retry_result)
        for num, r in retry_result.items():
            if is_sucesso(r):
                buffer.append((num, r))
                if len(buffer) >= batch_parquet:
                    flush_parquet_batch(modalidade, out_dir, buffer, arquivos)
                    buffer.clear()

    flush_parquet_batch(modalidade, out_dir, buffer, arquivos)

    trans_finais = [n for n, r in por_numero.items() if is_transiente(r)]
    return {
        "modo": "incremental",
        "total_esperado": None,
        "por_numero": por_numero,
        "sucessos": sum(1 for r in por_numero.values() if is_sucesso(r)),
        "ausentes": sum(1 for r in por_numero.values() if is_ausente(r)),
        "trans_restantes": len(trans_finais),
        "arquivos": arquivos,
        "concurso_inicio": min(por_numero.keys()) if por_numero else None,
        "concurso_fim": max(por_numero.keys()) if por_numero else None,
    }


async def run_async(modalidade: str) -> dict:
    iniciado = datetime.now(UTC)
    id_tj = id_tipo_jogo(modalidade)
    secao(f"COLETA CONCURSOS {modalidade}")
    log(f"inicio UTC {iniciado.isoformat()}")

    max_db = await asyncio.to_thread(db_max_numero_concurso, id_tj)
    tipo = "historico" if max_db is None else "incremental"
    evento("contexto", id_tipo_jogo=id_tj, max_db=max_db, modo=tipo)

    esperado = concurso_esperado_hoje(modalidade, max_db)
    if esperado is not None:
        log(f"calendario: hoje e dia de sorteio de {modalidade}; esperado >= concurso {esperado}")
    elif not deveria_ter_sorteio_hoje(modalidade):
        log(f"calendario: hoje nao e dia de sorteio de {modalidade}")

    skip_incremental = False
    if tipo == "incremental":
        prox = await asyncio.to_thread(db_data_proximo_concurso, id_tj)
        hoje = date.today()
        if prox is not None and prox > hoje:
            log(
                f"calendario: proximo sorteio previsto pra {prox} (em {(prox - hoje).days} "
                f"dia(s)) - sem incremental hoje"
            )
            skip_incremental = True
        elif prox is not None:
            log(
                f"calendario: proximo sorteio previsto pra {prox} (hoje ou antes) - tentando incremental"
            )

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if tipo == "historico":
            resumo = await coleta_historico(modalidade, session)
        elif skip_incremental:
            resumo = {
                "modo": "incremental",
                "total_esperado": None,
                "por_numero": {},
                "sucessos": 0,
                "ausentes": 0,
                "trans_restantes": 0,
                "arquivos": [],
                "concurso_inicio": None,
                "concurso_fim": None,
            }
        else:
            resumo = await coleta_incremental(modalidade, session, max_db + 1)

    resumo["iniciado_em"] = iniciado
    resumo["finalizado_em"] = datetime.now(UTC)
    resumo["tipo"] = tipo
    resumo["concurso_esperado"] = esperado
    return resumo


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Coleta bronze de concursos.")
    p.add_argument(
        "--modalidade",
        default="MEGA_SENA",
        help="Modalidade configurada em config/loterias.yaml (default: MEGA_SENA)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    modalidade = args.modalidade
    set_context(modalidade, "bronze.concursos")

    try:
        resumo = asyncio.run(run_async(modalidade))
    except Exception as exc:
        print(f"Erro fatal na coleta: {exc}", file=sys.stderr)
        return 1

    tipo = resumo["tipo"]
    total_s = resumo["sucessos"]
    total_e = resumo["total_esperado"]
    n_aus = resumo["ausentes"]
    n_tr = resumo["trans_restantes"]
    arquivos: list[tuple[Path, int, int, int]] = resumo["arquivos"]
    por_numero: dict[int, dict] = resumo["por_numero"]
    esperado = resumo.get("concurso_esperado")

    if total_s == 0 and n_tr == 0 and esperado is None:
        status = "sucesso"
    elif total_s == 0:
        status = "erro"
    elif n_tr > 0 or n_aus > 0:
        status = "parcial"
    else:
        status = "sucesso"

    secao("FECHAMENTO")
    log("gravando pipeline.execucao e pipeline.execucao_arquivo...")
    try:
        id_exec = db_insert_execucao_e_arquivos(
            modalidade,
            tipo=tipo,
            concurso_inicio=resumo["concurso_inicio"],
            concurso_fim=resumo["concurso_fim"],
            total_coletados=total_s,
            total_erros=n_tr,
            total_sem_dados=n_aus,
            concurso_esperado=esperado,
            status=status,
            iniciado_em=resumo["iniciado_em"],
            finalizado_em=resumo["finalizado_em"],
            arquivos=arquivos,
        )
    except Exception as exc:
        print(f"Erro ao gravar pipeline.execucao: {exc}", file=sys.stderr)
        return 1

    log("atualizando pipeline.concurso_fila...")
    try:
        fila_update_batch(modalidade, por_numero, id_exec)
    except Exception as exc:
        print(f"Erro ao atualizar fila: {exc}", file=sys.stderr)
        return 1

    ratio = f"{total_s}/{total_e}" if total_e is not None else f"{total_s} (incremental)"
    secao("RESUMO")
    evento(
        "fim",
        modalidade=modalidade,
        modo=tipo,
        coletados=ratio,
        arquivos=len(arquivos),
        http_404=n_aus,
        transitorios=n_tr,
        esperado=esperado,
        status=status,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
