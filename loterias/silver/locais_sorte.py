from __future__ import annotations

import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from loterias import caminhos
from loterias.db import conexao, devolver, emprestar
from loterias.log import aviso, evento, log, secao, set_context
from loterias.silver.ibge import corrigir_municipio
from loterias.silver.ibge import lookup_uf as _ibge_lookup_uf


def bronze_to_silver_dir(bronze_path: Path) -> Path:
    rel = bronze_path.resolve().relative_to((caminhos.bronze()).resolve())
    return caminhos.silver() / rel.parent / bronze_path.stem


FONTE = "locais_sorte"
STATUS_PENDENTE = "pendente"
STATUS_PROCESSANDO = "processando"
STATUS_CONCLUIDO = "concluido"
STATUS_ERRO = "erro"


def _reset_processando_orfaos() -> int:

    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline.execucao_arquivo
                SET status_prata = 'pendente'
                WHERE fonte = %s AND status_prata = 'processando'
                """,
                (FONTE,),
            )
            n = cur.rowcount
        conn.commit()
        return n


def _strip_accents(s: str) -> str:
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")


def _normalize_nome(s: str | None) -> str:
    if not s:
        return ""
    return _strip_accents(re.sub(r"\s+", " ", str(s).strip()).upper())


def parse_cidade_uf(texto: str | None) -> tuple[str, str]:
    if not texto or not str(texto).strip():
        return ("NAO INFORMADO", "NA")
    partes = [p.strip() for p in str(texto).rsplit("/", 1)]
    if len(partes) != 2 or not partes[0] or not partes[1]:
        return ("NAO INFORMADO", "NA")
    mun = _strip_accents(re.sub(r"\s+", " ", partes[0]).upper())
    uf = partes[1].upper().strip()[:2]
    if len(uf) == 2 and uf.isalpha():
        return corrigir_municipio(mun, uf)
    if not mun or "DIGITAL" in mun:
        return ("NAO INFORMADO", "NA")
    prefixo = uf if uf.isalpha() else None
    achou = _ibge_lookup_uf(mun, prefixo)
    if achou:
        return corrigir_municipio(mun, achou)
    return ("NAO INFORMADO", "NA")


def parse_premio_br(s: str | None) -> float | None:
    if not s:
        return None
    cleaned = str(s).strip()
    if cleaned.upper().startswith("R$"):
        cleaned = cleaned[2:].strip()
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def parse_teimosinha(v: str | None) -> bool | None:
    if v is None:
        return None
    if str(v).strip().lower() in ("sim", "true", "1"):
        return True
    if str(v).strip().lower() in ("não", "nao", "false", "0"):
        return False
    return None


def parse_int_str(v: str | int | None) -> int | None:
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def transform_payloads(
    rows: list[tuple[int, list]],
) -> dict[str, pd.DataFrame]:
    loterica_map: dict[tuple[str, str, str, str], dict] = {}
    ganhadores: list[dict] = []

    for numero_concurso, records in rows:
        for rec in records:
            nome_fantasia = _normalize_nome(rec.get("unidadeLoterica"))

            razao_social_raw = _normalize_nome(rec.get("razaoSocial"))
            razao_social = razao_social_raw or nome_fantasia

            canal_vendas = str(rec.get("canalVendas") or "").strip()
            municipio, uf = parse_cidade_uf(rec.get("cidade"))

            if not nome_fantasia:
                continue

            key = (nome_fantasia, canal_vendas, municipio, uf)
            if key not in loterica_map:
                loterica_map[key] = {
                    "razao_social": razao_social,
                    "nome_fantasia": nome_fantasia,
                    "municipio": municipio,
                    "uf": uf,
                    "canal_vendas": canal_vendas,
                }

            ganhadores.append(
                {
                    "numero_concurso": numero_concurso,
                    "razao_social": razao_social,
                    "nome_fantasia": nome_fantasia,
                    "canal_vendas": canal_vendas,
                    "municipio": municipio,
                    "uf": uf,
                    "faixa_acertos": str(rec.get("faixaAcertos") or "").strip(),
                    "numero_cotas": parse_int_str(rec.get("numeroCotas")),
                    "premio_total": parse_premio_br(rec.get("premioTotal")),
                    "quantidade_numeros_apostados": parse_int_str(
                        rec.get("quantidadeNumerosApostados")
                    ),
                    "quantidade_premios_por_faixa": parse_int_str(
                        rec.get("quantidadePremiosPorFaixa")
                    ),
                    "teimosinha": parse_teimosinha(rec.get("teimosinha")),
                    "tipo_aposta": str(rec.get("tipoAposta") or "").strip() or None,
                }
            )

    return {
        "loterica": pd.DataFrame(list(loterica_map.values())),
        "ganhador_loterica": pd.DataFrame(ganhadores),
    }


def processar_arquivo(caminho: str) -> None:
    bronze_path = caminhos.localizar(caminho)
    if not bronze_path.is_file():
        raise FileNotFoundError(f"Bronze nao encontrado: {bronze_path}")

    log(f"lendo {bronze_path.name} ...")
    df_bronze = pd.read_parquet(bronze_path)

    rows: list[tuple[int, list]] = []
    for _, row in df_bronze.iterrows():
        n = int(row["numero_concurso"])
        records = json.loads(row["payload"])
        if records:
            rows.append((n, records))

    if not rows:
        log("  (nenhum registro - arquivo vazio)")
        tabelas: dict[str, pd.DataFrame] = {
            "loterica": pd.DataFrame(),
            "ganhador_loterica": pd.DataFrame(),
        }
    else:
        tabelas = transform_payloads(rows)

    out_dir = bronze_to_silver_dir(bronze_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    for nome, df in tabelas.items():
        dest = out_dir / f"{nome}.parquet"
        df.to_parquet(dest, engine="pyarrow", index=False)
        evento("silver_salvo", tabela=f"{nome}.parquet", linhas=len(df))


def fetch_arquivos_pendentes(modalidade: str | None = None) -> list[tuple[int, str, int, int]]:
    conn = emprestar()
    try:
        with conn.cursor() as cur:
            if modalidade:
                cur.execute(
                    """
                    SELECT ea.id_arquivo, ea.caminho_arquivo, ea.concurso_inicio, ea.concurso_fim
                    FROM pipeline.execucao_arquivo ea
                    JOIN pipeline.execucao e ON e.id_execucao = ea.id_execucao
                    WHERE ea.fonte = %s
                      AND ea.status_prata IN (%s, %s)
                      AND e.modalidade = %s
                    ORDER BY ea.concurso_inicio
                    """,
                    (FONTE, STATUS_PENDENTE, STATUS_ERRO, modalidade),
                )
            else:
                cur.execute(
                    """
                    SELECT ea.id_arquivo, ea.caminho_arquivo, ea.concurso_inicio, ea.concurso_fim
                    FROM pipeline.execucao_arquivo ea
                    WHERE ea.fonte = %s
                      AND ea.status_prata IN (%s, %s)
                    ORDER BY ea.concurso_inicio
                    """,
                    (FONTE, STATUS_PENDENTE, STATUS_ERRO),
                )
            return list(cur.fetchall())
    finally:
        devolver(conn)


def update_status_prata(
    id_arquivo: int,
    status: str,
    *,
    erro: str | None = None,
    processado: bool = False,
) -> None:
    conn = emprestar()
    try:
        with conn.cursor() as cur:
            if processado:
                cur.execute(
                    """
                    UPDATE pipeline.execucao_arquivo
                    SET status_prata = %s,
                        processado_em = %s,
                        erro_prata = %s
                    WHERE id_arquivo = %s
                    """,
                    (status, datetime.now(UTC), erro, id_arquivo),
                )
            else:
                cur.execute(
                    """
                    UPDATE pipeline.execucao_arquivo
                    SET status_prata = %s, erro_prata = %s
                    WHERE id_arquivo = %s
                    """,
                    (status, erro, id_arquivo),
                )
        conn.commit()
    finally:
        devolver(conn)


def processar_lote_db(id_arquivo: int, caminho: str, ci: int, cf: int) -> None:
    secao(f"silver arquivo id={id_arquivo} concursos {ci}-{cf}")
    update_status_prata(id_arquivo, STATUS_PROCESSANDO)
    try:
        processar_arquivo(caminho)
        update_status_prata(id_arquivo, STATUS_CONCLUIDO, processado=True)
        evento("silver_ok", id_arquivo=id_arquivo, concursos=f"{ci}-{cf}")
    except Exception as e:
        msg = str(e)[:2000]
        aviso(f"silver id_arquivo={id_arquivo}: {msg}")
        update_status_prata(id_arquivo, STATUS_ERRO, processado=True, erro=msg)
        raise


def main_db(modalidade: str | None = None) -> int:
    set_context(modalidade or "TODOS", "silver.locais_sorte")

    orfaos = _reset_processando_orfaos()
    if orfaos:
        log(f"resetados {orfaos} arquivo(s) 'processando' orfaos")

    pendentes = fetch_arquivos_pendentes(modalidade)
    if not pendentes:
        log("nenhum arquivo locais_sorte pendente para Silver")
        return 0

    secao(f"SILVER LOCAIS_SORTE - {len(pendentes)} arquivo(s)")
    erros = 0
    for id_arquivo, caminho, ci, cf in pendentes:
        try:
            processar_lote_db(id_arquivo, caminho, ci, cf)
        except Exception:
            erros += 1

    secao("RESUMO")
    evento("fim", sucesso=len(pendentes) - erros, erros=erros)
    return 1 if erros else 0


def main_standalone() -> int:
    set_context("STANDALONE", "silver.locais_sorte")
    bronze_root = caminhos.bronze() / "megasena" / "locais_sorte"
    if not bronze_root.exists():
        aviso(f"pasta bronze nao encontrada: {bronze_root}")
        return 1

    files = sorted(bronze_root.rglob("*.parquet"))
    if not files:
        log("nenhum arquivo bronze locais_sorte encontrado")
        return 0

    secao(f"STANDALONE - {len(files)} arquivo(s)")
    erros = 0
    for f in files:
        log(f"processando {f.name}")
        try:
            processar_arquivo(str(f))
        except Exception as e:
            aviso(f"{f.name}: {e}")
            erros += 1

    evento("fim", sucesso=len(files) - erros, erros=erros)
    return 1 if erros else 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Bronze locais_sorte -> Silver.")
    parser.add_argument(
        "--no-db",
        "--standalone",
        action="store_true",
        dest="standalone",
        help="Modo standalone: varre data/bronze sem consultar DB",
    )
    parser.add_argument(
        "--modalidade", default=None, help="Opcional: filtrar por modalidade (ex: MEGA_SENA)"
    )
    args = parser.parse_args()

    if args.standalone:
        return main_standalone()
    return main_db(args.modalidade)


if __name__ == "__main__":
    raise SystemExit(main())
