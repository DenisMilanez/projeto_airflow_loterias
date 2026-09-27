from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import aiohttp
import pandas as pd
from psycopg2.extras import execute_batch

from loterias import caminhos
from loterias.config import (
    codigo_api,
    fila_max_tentativas,
    fila_proximo_retry,
    id_tipo_jogo,
    locais_sorte_batch_parquet,
    locais_sorte_concurso_min,
    locais_sorte_max_retries_inline,
    locais_sorte_pausa_apos_403,
    locais_sorte_pausa_apos_429,
    locais_sorte_request_sleep,
)
from loterias.db import conexao
from loterias.http import PORTAL_LOTERIAS, cabecalhos
from loterias.log import aviso, evento, log, secao, set_context

TIPO = "locais_sorte"
FONTE = "locais_sorte"
URL_BASE = "https://servicebus3.caixa.gov.br/portaldeloterias/api/locais-da-sorte"

DEFAULT_HEADERS = cabecalhos({"Accept": "application/json, text/plain, */*", **PORTAL_LOTERIAS})

BATCH_FILA = 100
SEMAPHORE_LIMIT = 1

MAX_PAGINAS_API = 100


def db_reset_processando_orfaos(modalidade: str) -> int:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline.locais_sorte_fila
                SET status='pendente', atualizado_em=NOW()
                WHERE modalidade=%s AND status='processando'
                """,
                (modalidade,),
            )
            resetados = cur.rowcount
        conn.commit()
        return resetados


def db_seed_fila(modalidade: str, id_tj: int, concurso_min: int | None) -> int:
    max_tent = fila_max_tentativas()
    with conexao() as conn:
        with conn.cursor() as cur:
            if concurso_min is None:
                cur.execute(
                    """
                    INSERT INTO pipeline.locais_sorte_fila (modalidade, numero_concurso, max_tentativas)
                    SELECT %s, c.numero_concurso, %s
                    FROM public.concurso c
                    WHERE c.id_tipo_jogo = %s
                      AND NOT EXISTS (
                          SELECT 1 FROM pipeline.locais_sorte_fila f
                          WHERE f.modalidade=%s AND f.numero_concurso=c.numero_concurso
                      )
                    """,
                    (modalidade, max_tent, id_tj, modalidade),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO pipeline.locais_sorte_fila (modalidade, numero_concurso, max_tentativas)
                    SELECT %s, c.numero_concurso, %s
                    FROM public.concurso c
                    WHERE c.id_tipo_jogo = %s
                      AND c.numero_concurso >= %s
                      AND NOT EXISTS (
                          SELECT 1 FROM pipeline.locais_sorte_fila f
                          WHERE f.modalidade=%s AND f.numero_concurso=c.numero_concurso
                      )
                    """,
                    (modalidade, max_tent, id_tj, concurso_min, modalidade),
                )
            inseridos = cur.rowcount
        conn.commit()
        return inseridos


def db_fetch_pendentes(modalidade: str, limit: int) -> list[tuple[int, int]]:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, numero_concurso
                FROM pipeline.locais_sorte_fila
                WHERE modalidade=%s
                  AND status IN ('pendente', 'erro')
                  AND (proximo_retry_em IS NULL OR proximo_retry_em <= NOW())
                ORDER BY numero_concurso
                LIMIT %s
                """,
                (modalidade, limit),
            )
            return list(cur.fetchall())


def db_mark_processando(ids: list[int]) -> None:
    if not ids:
        return
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline.locais_sorte_fila
                SET status='processando', ultima_tentativa_em=NOW(), atualizado_em=NOW()
                WHERE id = ANY(%s)
                """,
                (ids,),
            )
        conn.commit()


def db_create_execucao(
    modalidade: str, iniciado_em: datetime, concurso_esperado: int | None
) -> int:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pipeline.execucao (
                    modalidade, tipo, status, iniciado_em, fonte, concurso_esperado
                ) VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id_execucao
                """,
                (modalidade, TIPO, "processando", iniciado_em, FONTE, concurso_esperado),
            )
            id_exec = cur.fetchone()[0]
        conn.commit()
        return id_exec


def db_update_execucao(
    id_execucao: int,
    *,
    concurso_inicio: int | None,
    concurso_fim: int | None,
    total_coletados: int,
    total_erros: int,
    total_sem_dados: int,
    status: str,
    mensagem_erro: str | None,
    finalizado_em: datetime,
) -> None:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline.execucao
                SET concurso_inicio=%s, concurso_fim=%s,
                    total_coletados=%s, total_erros=%s, total_sem_dados=%s,
                    status=%s, mensagem_erro=%s, finalizado_em=%s
                WHERE id_execucao=%s
                """,
                (
                    concurso_inicio,
                    concurso_fim,
                    total_coletados,
                    total_erros,
                    total_sem_dados,
                    status,
                    mensagem_erro,
                    finalizado_em,
                    id_execucao,
                ),
            )
        conn.commit()


def db_register_arquivo(id_execucao: int, path: Path, ci: int, cf: int, total: int) -> int:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pipeline.execucao_arquivo (
                    id_execucao, caminho_arquivo, concurso_inicio, concurso_fim,
                    total_registros, fonte
                ) VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id_arquivo
                """,
                (id_execucao, str(path.resolve()), ci, cf, total, FONTE),
            )
            id_arquivo = cur.fetchone()[0]
        conn.commit()
        return id_arquivo


def db_update_fila_concluido_batch(fila_ids: list[int], id_arquivo: int) -> None:
    if not fila_ids:
        return
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline.locais_sorte_fila
                SET status='concluido', id_arquivo=%s, atualizado_em=NOW()
                WHERE id = ANY(%s)
                """,
                (id_arquivo, fila_ids),
            )
        conn.commit()


def db_update_fila_sem_dados_batch(fila_ids: list[int]) -> None:
    if not fila_ids:
        return
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline.locais_sorte_fila
                SET status='sem_dados', atualizado_em=NOW()
                WHERE id = ANY(%s)
                """,
                (fila_ids,),
            )
        conn.commit()


def db_update_fila_parcial(
    fila_id: int, id_arquivo: int, coletados: int, total_api: int | None
) -> None:
    detalhe = (
        f"parcial: {coletados}/{total_api} registros coletados "
        f"(teto de {MAX_PAGINAS_API} paginas da API da Caixa)"
    )
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline.locais_sorte_fila
                SET status='parcial', id_arquivo=%s, erro_detalhe=%s,
                    atualizado_em=NOW()
                WHERE id = %s
                """,
                (id_arquivo, detalhe, fila_id),
            )
        conn.commit()


def db_update_fila_erro_batch(erros: list[tuple[int, str]]) -> None:
    if not erros:
        return
    now = datetime.now(UTC)
    ids = [e[0] for e in erros]

    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, tentativas, max_tentativas FROM pipeline.locais_sorte_fila WHERE id = ANY(%s)",
                (ids,),
            )
            atuais = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

            updates = []
            for fila_id, detalhe in erros:
                t_atual, max_tent = atuais.get(fila_id, (0, fila_max_tentativas()))
                t_nova = t_atual + 1
                if t_nova >= max_tent:
                    novo_status = "abandonado"
                    proximo = None
                else:
                    novo_status = "erro"
                    proximo = fila_proximo_retry(t_nova, now)
                updates.append((novo_status, t_nova, now, proximo, detalhe[:2000], now, fila_id))

            execute_batch(
                cur,
                """
                UPDATE pipeline.locais_sorte_fila
                SET status=%s, tentativas=%s,
                    ultima_tentativa_em=%s, proximo_retry_em=%s,
                    erro_detalhe=%s, atualizado_em=%s
                WHERE id=%s
                """,
                updates,
                page_size=200,
            )
        conn.commit()


async def fetch_locais_concurso(
    session: aiohttp.ClientSession,
    modalidade: str,
    numero: int,
    fila_id: int,
    sem: asyncio.Semaphore,
    request_sleep: float,
    pausa_429: float,
    pausa_403: float,
    max_retries_inline: int,
) -> dict[str, Any]:
    async with sem:
        if request_sleep > 0:
            await asyncio.sleep(request_sleep)

        all_records: list[dict] = []
        pagina = 1
        total_registros: int | None = None
        inline_retries = 0

        while True:
            ts = int(time.time() * 1000)
            url = (
                f"{URL_BASE}?modalidade={modalidade}&concurso={numero}"
                f"&pageSize=1000&pagina={pagina}&_={ts}"
            )
            try:
                timeout = aiohttp.ClientTimeout(total=60)
                async with session.get(url, headers=DEFAULT_HEADERS, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        if isinstance(data, dict):
                            registros = data.get("locaisDaSorte") or []
                            if total_registros is None:
                                total_registros = int(data.get("totalRegistros") or 0)
                        elif isinstance(data, list):
                            registros = data
                            if total_registros is None:
                                total_registros = len(data)
                        else:
                            return {
                                "status": "erro",
                                "fila_id": fila_id,
                                "numero": numero,
                                "detalhe": f"tipo inesperado: {type(data).__name__}",
                            }

                        all_records.extend(registros)

                        if total_registros is not None and len(all_records) >= total_registros:
                            break
                        if len(registros) < 1000:
                            if total_registros and len(all_records) < total_registros:
                                return {
                                    "status": "erro",
                                    "fila_id": fila_id,
                                    "numero": numero,
                                    "detalhe": (
                                        f"paginacao incompleta: {len(all_records)}/"
                                        f"{total_registros} (pag {pagina})"
                                    ),
                                }
                            break
                        log(
                            f"  ... concurso {numero} pag {pagina} ok ({len(registros)} regs), "
                            f"continuando paginacao..."
                        )
                        if request_sleep > 0:
                            await asyncio.sleep(request_sleep)
                        pagina += 1

                    elif resp.status in (404, 204):
                        return {"status": "sem_dados", "fila_id": fila_id, "numero": numero}

                    elif resp.status == 429:
                        inline_retries += 1
                        if inline_retries > max_retries_inline:
                            return {
                                "status": "erro",
                                "fila_id": fila_id,
                                "numero": numero,
                                "detalhe": f"HTTP 429 apos {max_retries_inline} retries inline (vai pra fila)",
                            }
                        aviso(
                            f"429 concurso {numero} pag {pagina} - pausa {pausa_429:.0f}s "
                            f"(retry {inline_retries}/{max_retries_inline})"
                        )
                        await asyncio.sleep(pausa_429)
                        continue

                    elif resp.status == 403:
                        inline_retries += 1
                        if inline_retries > max_retries_inline:
                            return {
                                "status": "erro",
                                "fila_id": fila_id,
                                "numero": numero,
                                "detalhe": f"HTTP 403 apos {max_retries_inline} retries inline (vai pra fila — provável ban)",
                            }
                        aviso(
                            f"403 concurso {numero} pag {pagina} - pausa {pausa_403:.0f}s "
                            f"(retry {inline_retries}/{max_retries_inline})"
                        )
                        await asyncio.sleep(pausa_403)
                        continue

                    else:
                        if pagina > MAX_PAGINAS_API and all_records:
                            evento(
                                "concurso_parcial",
                                numero=numero,
                                locais=len(all_records),
                                paginas=pagina - 1,
                                total_api=total_registros,
                                motivo=f"teto de {MAX_PAGINAS_API} paginas da API",
                            )
                            return {
                                "status": "parcial",
                                "fila_id": fila_id,
                                "numero": numero,
                                "records": all_records,
                                "total_api": total_registros,
                            }
                        return {
                            "status": "erro",
                            "fila_id": fila_id,
                            "numero": numero,
                            "detalhe": f"HTTP {resp.status}",
                        }
            except TimeoutError:
                return {
                    "status": "erro",
                    "fila_id": fila_id,
                    "numero": numero,
                    "detalhe": "timeout",
                }
            except Exception as e:
                return {
                    "status": "erro",
                    "fila_id": fila_id,
                    "numero": numero,
                    "detalhe": str(e)[:200],
                }

        if not all_records:
            return {"status": "sem_dados", "fila_id": fila_id, "numero": numero}
        evento("concurso_ok", numero=numero, locais=len(all_records), paginas=pagina)
        return {"status": "sucesso", "fila_id": fila_id, "numero": numero, "records": all_records}


def bronze_out_dir(modalidade: str) -> Path:
    jogo = codigo_api(modalidade)
    today = date.today()
    return (
        caminhos.bronze()
        / jogo
        / "locais_sorte"
        / f"{today.year:04d}"
        / f"{today.month:02d}"
        / f"{today.day:02d}"
    )


def flush_parquet_batch(
    modalidade: str,
    id_execucao: int,
    buffer: list[tuple[int, int, list]],
    arquivos_gerados: list[tuple[Path, int, int, int]],
) -> None:
    if not buffer:
        return
    buffer_sorted = sorted(buffer, key=lambda x: x[1])
    n0 = buffer_sorted[0][1]
    n1 = buffer_sorted[-1][1]
    today_str = date.today().strftime("%Y%m%d")
    jogo = codigo_api(modalidade)

    out_dir = bronze_out_dir(modalidade)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{jogo}_locais_sorte_{n0:04d}_{n1:04d}_{today_str}.parquet"

    rows = [
        {"numero_concurso": n, "payload": json.dumps(records, ensure_ascii=False)}
        for _, n, records in buffer_sorted
    ]
    pd.DataFrame(rows).to_parquet(path, engine="pyarrow", index=False)
    evento("parquet_salvo", arquivo=path.name, concursos=len(buffer_sorted), intervalo=f"{n0}-{n1}")

    id_arquivo = db_register_arquivo(id_execucao, path, n0, n1, len(buffer_sorted))
    fila_ids = [fila_id for fila_id, _, _ in buffer_sorted]
    db_update_fila_concluido_batch(fila_ids, id_arquivo)
    arquivos_gerados.append((path, n0, n1, len(buffer_sorted)))


def flush_parcial(
    modalidade: str,
    id_execucao: int,
    fila_id: int,
    numero: int,
    records: list,
    total_api: int | None,
    arquivos_gerados: list[tuple[Path, int, int, int]],
) -> None:
    today_str = date.today().strftime("%Y%m%d")
    jogo = codigo_api(modalidade)
    out_dir = bronze_out_dir(modalidade)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{jogo}_locais_sorte_{numero:04d}_{numero:04d}_PARCIAL_{today_str}.parquet"

    rows = [{"numero_concurso": numero, "payload": json.dumps(records, ensure_ascii=False)}]
    pd.DataFrame(rows).to_parquet(path, engine="pyarrow", index=False)
    evento("parquet_parcial_salvo", arquivo=path.name, registros=len(records), total_api=total_api)

    id_arquivo = db_register_arquivo(id_execucao, path, numero, numero, 1)
    db_update_fila_parcial(fila_id, id_arquivo, len(records), total_api)
    arquivos_gerados.append((path, numero, numero, 1))


async def run_async(modalidade: str) -> dict[str, Any]:
    iniciado = datetime.now(UTC)
    id_tj = id_tipo_jogo(modalidade)
    concurso_min = locais_sorte_concurso_min(modalidade)
    request_sleep = locais_sorte_request_sleep(modalidade)
    pausa_429 = locais_sorte_pausa_apos_429(modalidade)
    pausa_403 = locais_sorte_pausa_apos_403(modalidade)
    max_retries = locais_sorte_max_retries_inline(modalidade)
    batch_parquet = locais_sorte_batch_parquet(modalidade)

    secao(f"COLETA LOCAIS DA SORTE {modalidade}")
    log(f"inicio UTC {iniciado.isoformat()}")
    evento(
        "config",
        sleep=f"{request_sleep}s",
        reqmin=f"~{60 / request_sleep:.0f}/min",
        pausa_429=f"{pausa_429:.0f}s",
        pausa_403=f"{pausa_403:.0f}s",
        max_retries_inline=max_retries,
        batch_parquet=batch_parquet,
    )

    orfaos = await asyncio.to_thread(db_reset_processando_orfaos, modalidade)
    if orfaos:
        log(f"fila: resetadas {orfaos} entrada(s) 'processando' orfas")

    novos = await asyncio.to_thread(db_seed_fila, modalidade, id_tj, concurso_min)
    if novos:
        log(f"fila: {novos} novo(s) concurso(s) seed-ado(s)")
    else:
        log("fila: nenhum concurso novo no seed")

    items_probe = await asyncio.to_thread(db_fetch_pendentes, modalidade, 1)
    if not items_probe:
        log("nenhum item pendente na fila - encerrando")
        return {
            "id_execucao": None,
            "total_sucesso": 0,
            "total_parcial": 0,
            "total_sem_dados": 0,
            "total_erro": 0,
            "arquivos": [],
            "primeiro_concurso": None,
            "ultimo_concurso": None,
            "iniciado_em": iniciado,
            "finalizado_em": datetime.now(UTC),
        }

    id_execucao = await asyncio.to_thread(db_create_execucao, modalidade, iniciado, None)
    evento("execucao_criada", id_execucao=id_execucao)

    total_sucesso = 0
    total_sem_dados = 0
    total_erro = 0
    total_parcial = 0
    arquivos_gerados: list[tuple[Path, int, int, int]] = []
    buffer: list[tuple[int, int, list]] = []
    primeiro_concurso: int | None = None
    ultimo_concurso: int | None = None

    sem = asyncio.Semaphore(SEMAPHORE_LIMIT)
    timeout_global = aiohttp.ClientTimeout(total=60)

    todos_pendentes = await asyncio.to_thread(db_fetch_pendentes, modalidade, 999_999)
    ids_desta_execucao = {fila_id for fila_id, _ in todos_pendentes}
    evento("fila_carregada", elegiveis=len(ids_desta_execucao))

    async with aiohttp.ClientSession(timeout=timeout_global) as session:
        while True:
            all_items = await asyncio.to_thread(db_fetch_pendentes, modalidade, 999_999)
            items = [(fid, n) for fid, n in all_items if fid in ids_desta_execucao]
            if not items:
                break
            items = items[:BATCH_FILA]
            secao(f"batch de {len(items)} itens (concursos {items[0][1]}..{items[-1][1]})")

            for fila_id, n in items:
                await asyncio.to_thread(db_mark_processando, [fila_id])
                r = await fetch_locais_concurso(
                    session,
                    modalidade,
                    n,
                    fila_id,
                    sem,
                    request_sleep,
                    pausa_429,
                    pausa_403,
                    max_retries,
                )
                ids_desta_execucao.discard(fila_id)

                s = r["status"]
                if s == "sucesso":
                    buffer.append((fila_id, n, r["records"]))
                    total_sucesso += 1
                    if primeiro_concurso is None or n < primeiro_concurso:
                        primeiro_concurso = n
                    if ultimo_concurso is None or n > ultimo_concurso:
                        ultimo_concurso = n
                    if len(buffer) >= batch_parquet:
                        await asyncio.to_thread(
                            flush_parquet_batch, modalidade, id_execucao, buffer, arquivos_gerados
                        )
                        buffer.clear()
                elif s == "parcial":
                    await asyncio.to_thread(
                        flush_parcial,
                        modalidade,
                        id_execucao,
                        fila_id,
                        n,
                        r["records"],
                        r.get("total_api"),
                        arquivos_gerados,
                    )
                    total_parcial += 1
                    if primeiro_concurso is None or n < primeiro_concurso:
                        primeiro_concurso = n
                    if ultimo_concurso is None or n > ultimo_concurso:
                        ultimo_concurso = n
                    aviso(
                        f"concurso {n}: PARCIAL — {len(r['records'])} de "
                        f"~{r.get('total_api')} registros (teto da API). "
                        f"Marcado terminal, nao retenta."
                    )
                elif s == "sem_dados":
                    await asyncio.to_thread(db_update_fila_sem_dados_batch, [fila_id])
                    total_sem_dados += 1
                else:
                    await asyncio.to_thread(
                        db_update_fila_erro_batch, [(fila_id, r.get("detalhe", ""))]
                    )
                    total_erro += 1
                    aviso(f"concurso {n}: {r.get('detalhe', '')[:120]}")

            evento(
                "progresso",
                sucesso=total_sucesso,
                parcial=total_parcial,
                sem_dados=total_sem_dados,
                erro=total_erro,
                buffer=len(buffer),
            )

    if buffer:
        flush_parquet_batch(modalidade, id_execucao, buffer, arquivos_gerados)

    finalizado = datetime.now(UTC)
    return {
        "id_execucao": id_execucao,
        "total_sucesso": total_sucesso,
        "total_parcial": total_parcial,
        "total_sem_dados": total_sem_dados,
        "total_erro": total_erro,
        "arquivos": arquivos_gerados,
        "primeiro_concurso": primeiro_concurso,
        "ultimo_concurso": ultimo_concurso,
        "iniciado_em": iniciado,
        "finalizado_em": finalizado,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Coleta bronze locais da sorte.")
    p.add_argument(
        "--modalidade", default="MEGA_SENA", help="Modalidade configurada em config/loterias.yaml"
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    modalidade = args.modalidade
    set_context(modalidade, "bronze.locais_sorte")

    try:
        resumo = asyncio.run(run_async(modalidade))
    except Exception as e:
        print(f"Erro fatal na coleta: {e}", file=sys.stderr)
        return 1

    id_exec = resumo["id_execucao"]
    if id_exec is None:
        return 0

    total_s = resumo["total_sucesso"]
    total_p = resumo.get("total_parcial", 0)
    total_sd = resumo["total_sem_dados"]
    total_e = resumo["total_erro"]
    arquivos = resumo["arquivos"]

    status = "sucesso" if total_e == 0 else ("parcial" if total_s > 0 else "erro")
    mensagem: str | None = None
    if total_e > 0:
        mensagem = json.dumps({"erros": total_e}, ensure_ascii=False)

    try:
        db_update_execucao(
            id_exec,
            concurso_inicio=resumo["primeiro_concurso"],
            concurso_fim=resumo["ultimo_concurso"],
            total_coletados=total_s + total_p,
            total_erros=total_e,
            total_sem_dados=total_sd,
            status=status,
            mensagem_erro=mensagem,
            finalizado_em=resumo["finalizado_em"],
        )
    except Exception as e:
        print(f"Erro ao atualizar pipeline.execucao: {e}", file=sys.stderr)
        return 1

    secao("RESUMO")
    evento(
        "fim",
        modalidade=modalidade,
        coletados=total_s,
        parciais=total_p,
        sem_dados=total_sd,
        erros=total_e,
        arquivos=len(arquivos),
        status=status,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
