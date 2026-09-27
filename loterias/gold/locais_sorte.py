from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import pandas as pd
from psycopg2.extras import execute_batch

from loterias import caminhos
from loterias.config import id_tipo_jogo as cfg_id_tipo_jogo
from loterias.db import conexao, devolver, emprestar
from loterias.gold import normalizacao
from loterias.log import aviso, evento, log, secao, set_context
from loterias.silver.concursos import bronze_to_silver_dir
from loterias.silver.validacao import validar_pasta_silver

FONTE = "locais_sorte"


def _parse_acertos(s: str | None) -> int | None:
    if not s:
        return None
    m = re.search(r"(\d+)", str(s))
    return int(m.group(1)) if m else None


def reset_ouro_processando_orfaos() -> int:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline.execucao_arquivo
                SET status_ouro = NULL
                WHERE fonte = %s AND status_ouro = 'processando'
                """,
                (FONTE,),
            )
            n = cur.rowcount
        conn.commit()
        return n


def fetch_arquivos_prata_concluidos(
    modalidade: str | None = None,
) -> list[tuple[int, str, int, int, str]]:
    with conexao() as conn:
        with conn.cursor() as cur:
            if modalidade:
                cur.execute(
                    """
                    SELECT ea.id_arquivo, ea.caminho_arquivo, ea.concurso_inicio, ea.concurso_fim, e.modalidade
                    FROM pipeline.execucao_arquivo ea
                    JOIN pipeline.execucao e ON e.id_execucao = ea.id_execucao
                    WHERE ea.fonte = %s
                      AND ea.status_prata = 'concluido'
                      AND (ea.status_ouro IS NULL OR ea.status_ouro = 'erro')
                      AND e.modalidade = %s
                    ORDER BY ea.concurso_inicio
                    """,
                    (FONTE, modalidade),
                )
            else:
                cur.execute(
                    """
                    SELECT ea.id_arquivo, ea.caminho_arquivo, ea.concurso_inicio, ea.concurso_fim, e.modalidade
                    FROM pipeline.execucao_arquivo ea
                    JOIN pipeline.execucao e ON e.id_execucao = ea.id_execucao
                    WHERE ea.fonte = %s
                      AND ea.status_prata = 'concluido'
                      AND (ea.status_ouro IS NULL OR ea.status_ouro = 'erro')
                    ORDER BY ea.concurso_inicio
                    """,
                    (FONTE,),
                )
            return list(cur.fetchall())


def update_status_ouro(id_arquivo: int, status: str, erro: str | None = None) -> None:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline.execucao_arquivo
                SET status_ouro = %s,
                    erro_ouro = %s,
                    processado_ouro_em = CASE WHEN %s IN ('concluido','erro') THEN NOW() ELSE processado_ouro_em END
                WHERE id_arquivo = %s
                """,
                (status, erro, status, id_arquivo),
            )
        conn.commit()


QUALIDADE_ESTRITA = os.environ.get("LOTERIAS_QUALIDADE_ESTRITA", "0") == "1"


class LocaisGoldLoader:
    def __init__(self, id_tipo_jogo: int) -> None:
        self.conn = emprestar()
        self.id_tipo_jogo = int(id_tipo_jogo)
        self.localidade: dict[tuple[str, str], int] = {}
        self.loterica: dict[tuple[str, str, int], int] = {}
        self.faixa_por_acertos: dict[tuple[int, int], int] = {}
        self.concurso: dict[tuple[int, int], int] = {}
        self._hydrate_faixa()

    def close(self) -> None:
        devolver(self.conn)

    def _hydrate_faixa(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT id_faixa, id_tipo_jogo, acertos FROM public.faixa")
            for fid, tid, ac in cur.fetchall():
                self.faixa_por_acertos[(int(tid), int(ac))] = int(fid)

    def _refresh_localidade_cache(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT id_localidade, municipio, uf FROM public.localidade")
            for lid, mun, uf in cur.fetchall():
                self.localidade[(str(mun), str(uf))] = int(lid)

    def _refresh_loterica_cache(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id_loterica, razao_social, canal_vendas, id_localidade FROM public.loterica"
            )
            for lid, rs, cv, iloc in cur.fetchall():
                if iloc is not None:
                    self.loterica[(str(rs), str(cv), int(iloc))] = int(lid)

    def _refresh_concurso_cache(self, numeros: list[int]) -> None:
        if not numeros:
            return
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT numero_concurso, id_concurso
                FROM public.concurso
                WHERE id_tipo_jogo = %s AND numero_concurso = ANY(%s)
                """,
                (self.id_tipo_jogo, numeros),
            )
            for num, cid in cur.fetchall():
                self.concurso[(self.id_tipo_jogo, int(num))] = int(cid)

    def bulk_localidade(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        rows = list(dict.fromkeys((str(r["municipio"]), str(r["uf"])) for _, r in df.iterrows()))
        novos = [r for r in rows if r not in self.localidade]
        if not novos:
            return
        with self.conn.cursor() as cur:
            execute_batch(
                cur,
                """
                INSERT INTO public.localidade (municipio, uf)
                VALUES (%s, %s)
                ON CONFLICT (municipio, uf) DO NOTHING
                """,
                novos,
            )
        self.conn.commit()
        self._refresh_localidade_cache()

    def bulk_loterica(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        rows_insert = []
        for _, r in df.iterrows():
            mun = str(r["municipio"])
            uf = str(r["uf"])
            lid = self.localidade.get((mun, uf))
            if lid is None:
                aviso(f"localidade nao encontrada: ({mun}, {uf}) - pulando loterica")
                continue
            rs = str(r["razao_social"])
            cv = str(r["canal_vendas"])
            nf = str(r.get("nome_fantasia") or "")
            key = (rs, cv, lid)
            if key not in self.loterica:
                rows_insert.append((rs, nf or None, lid, cv))

        if rows_insert:
            rows_insert = list(dict.fromkeys(rows_insert))
            with self.conn.cursor() as cur:
                execute_batch(
                    cur,
                    """
                    INSERT INTO public.loterica (razao_social, nome_fantasia, id_localidade, canal_vendas)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (razao_social, canal_vendas, id_localidade) DO NOTHING
                    """,
                    rows_insert,
                )
            self.conn.commit()
            self._refresh_loterica_cache()

    def bulk_ganhador_loterica(self, df: pd.DataFrame) -> None:
        if df.empty:
            return

        numeros = df["numero_concurso"].unique().tolist()
        numeros_faltando = [n for n in numeros if (self.id_tipo_jogo, int(n)) not in self.concurso]
        if numeros_faltando:
            self._refresh_concurso_cache(numeros_faltando)

        rows: list[tuple] = []
        skipped = 0
        avisos_faixa: set[str] = set()
        for _, r in df.iterrows():
            mun = str(r["municipio"])
            uf = str(r["uf"])
            lid = self.localidade.get((mun, uf))
            if lid is None:
                skipped += 1
                continue

            rs = str(r["razao_social"])
            cv = str(r["canal_vendas"])
            id_loterica = self.loterica.get((rs, cv, lid))
            if id_loterica is None:
                skipped += 1
                continue

            fa_raw = str(r.get("faixa_acertos") or "").strip()
            acertos = _parse_acertos(fa_raw)
            id_faixa = (
                self.faixa_por_acertos.get((self.id_tipo_jogo, acertos))
                if acertos is not None
                else None
            )
            if id_faixa is None:
                if fa_raw not in avisos_faixa:
                    aviso(
                        f"faixa nao encontrada para '{fa_raw}' (acertos={acertos}) - pulando registros dessa faixa"
                    )
                    avisos_faixa.add(fa_raw)
                skipped += 1
                continue

            id_concurso = self.concurso.get((self.id_tipo_jogo, int(r["numero_concurso"])))
            if id_concurso is None:
                aviso(f"concurso nao encontrado: {r['numero_concurso']} - pulando")
                skipped += 1
                continue

            tipo_aposta = r.get("tipo_aposta")
            premio_total = r.get("premio_total")
            rows.append(
                (
                    id_concurso,
                    id_loterica,
                    id_faixa,
                    cv,
                    tipo_aposta if pd.notna(tipo_aposta) else None,
                    r.get("numero_cotas") if pd.notna(r.get("numero_cotas")) else None,
                    r.get("quantidade_numeros_apostados")
                    if pd.notna(r.get("quantidade_numeros_apostados"))
                    else None,
                    r.get("quantidade_premios_por_faixa")
                    if pd.notna(r.get("quantidade_premios_por_faixa"))
                    else None,
                    float(premio_total) if pd.notna(premio_total) else None,
                    bool(r["teimosinha"]) if pd.notna(r.get("teimosinha")) else None,
                )
            )

        if rows:
            with self.conn.cursor() as cur:
                execute_batch(
                    cur,
                    """
                    INSERT INTO public.ganhador_loterica (
                        id_concurso, id_loterica, id_faixa, canal_vendas, tipo_aposta,
                        numero_cotas, quantidade_numeros_apostados, quantidade_premios_por_faixa,
                        premio_total, teimosinha
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id_concurso, id_loterica, id_faixa, tipo_aposta) DO NOTHING
                    """,
                    rows,
                )
            self.conn.commit()
        evento("ganhador_loterica_inserido", inseridos=len(rows), pulados=skipped)

    def load_silver_dir(self, silver_dir: Path) -> None:
        log(f"carregando silver: {silver_dir.name}")
        validar_pasta_silver(silver_dir, estrito=QUALIDADE_ESTRITA)

        path_lot = silver_dir / "loterica.parquet"
        path_gan = silver_dir / "ganhador_loterica.parquet"

        if not path_lot.exists() or not path_gan.exists():
            aviso(f"parquets silver ausentes em {silver_dir}")
            return

        df_lot = pd.read_parquet(path_lot)
        df_gan = pd.read_parquet(path_gan)

        if df_lot.empty and df_gan.empty:
            log("silver vazio - sem ganhadores")
            return

        localidades_df = df_lot[["municipio", "uf"]].drop_duplicates()
        if not df_lot.empty:
            self.bulk_localidade(localidades_df)
        if not self.localidade:
            self._refresh_localidade_cache()

        self.bulk_loterica(df_lot)
        if not self.loterica:
            self._refresh_loterica_cache()

        self.bulk_ganhador_loterica(df_gan)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Silver locais_sorte -> Gold (PostgreSQL).")
    p.add_argument(
        "--modalidade", default=None, help="Opcional: filtrar por modalidade (ex: MEGA_SENA)"
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    set_context(args.modalidade or "TODOS", "gold.locais_sorte")

    orfaos = reset_ouro_processando_orfaos()
    if orfaos:
        log(f"resetados {orfaos} arquivo(s) 'processando' orfaos")

    arquivos = fetch_arquivos_prata_concluidos(args.modalidade)
    if not arquivos:
        log("nenhum arquivo locais_sorte com status_prata=concluido (e ouro pendente)")
        return 0

    secao(f"GOLD LOCAIS_SORTE - {len(arquivos)} arquivo(s) -> PostgreSQL")
    erros = 0
    loaders_por_modalidade: dict[str, LocaisGoldLoader] = {}
    try:
        for id_arquivo, caminho, ci, cf, modal in arquivos:
            secao(f"gold arquivo id={id_arquivo} concursos {ci}-{cf} modalidade={modal}")
            update_status_ouro(id_arquivo, "processando")

            if modal not in loaders_por_modalidade:
                id_tj = cfg_id_tipo_jogo(modal)
                loaders_por_modalidade[modal] = LocaisGoldLoader(id_tj)
            loader = loaders_por_modalidade[modal]

            silver_dir = bronze_to_silver_dir(caminhos.localizar(caminho))
            if not silver_dir.is_dir():
                msg = f"pasta silver nao encontrada: {silver_dir}"
                aviso(msg)
                update_status_ouro(id_arquivo, "erro", msg)
                erros += 1
                continue
            try:
                loader.load_silver_dir(silver_dir)
                update_status_ouro(id_arquivo, "concluido")
                evento("gold_ok", id_arquivo=id_arquivo, concursos=f"{ci}-{cf}", modalidade=modal)
            except Exception as e:
                msg = str(e)[:2000]
                aviso(f"gold id_arquivo={id_arquivo}: {msg}")
                update_status_ouro(id_arquivo, "erro", msg)
                erros += 1
                msg_lower = msg.lower()
                if any(
                    s in msg_lower
                    for s in (
                        "connection already closed",
                        "server closed the connection",
                        "ssl connection has been closed",
                        "connection is closed",
                        "could not connect",
                        "operationalerror",
                    )
                ):
                    aviso(
                        f"loader {modal} com conexao morta - descartando pra recriar no proximo arquivo"
                    )
                    try:
                        loader.close()
                    except Exception:
                        pass
                    loaders_por_modalidade.pop(modal, None)
    finally:
        for ldr in loaders_por_modalidade.values():
            ldr.close()

    with conexao() as conn:
        with conn.cursor() as cur:
            normalizados = normalizacao.normalizar(cur)
        conn.commit()
    evento("normalizacao", **vars(normalizados))

    secao("RESUMO")
    evento("fim", sucesso=len(arquivos) - erros, erros=erros)
    return 1 if erros else 0


if __name__ == "__main__":
    raise SystemExit(main())
