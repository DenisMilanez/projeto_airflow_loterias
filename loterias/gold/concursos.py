from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd
from psycopg2.extras import execute_batch

from loterias import caminhos
from loterias.db import conexao, devolver, emprestar
from loterias.gold import data_proximo, normalizacao
from loterias.log import aviso, evento, log, secao, set_context
from loterias.silver.concursos import bronze_to_silver_dir
from loterias.silver.validacao import validar_pasta_silver


def reset_ouro_processando_orfaos(fonte: str = "concursos") -> int:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline.execucao_arquivo
                SET status_ouro = NULL
                WHERE fonte = %s AND status_ouro = 'processando'
                """,
                (fonte,),
            )
            n = cur.rowcount
        conn.commit()
        return n


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


def _int_or_none(v) -> int | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return int(v)


def _num_or_none(v) -> float | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return float(v)


def fetch_arquivos_prata_concluidos(
    modalidade: str | None = None,
) -> list[tuple[int, str, int, int]]:
    with conexao() as conn:
        with conn.cursor() as cur:
            if modalidade:
                cur.execute(
                    """
                    SELECT ea.id_arquivo, ea.caminho_arquivo, ea.concurso_inicio, ea.concurso_fim
                    FROM pipeline.execucao_arquivo ea
                    JOIN pipeline.execucao e ON e.id_execucao = ea.id_execucao
                    WHERE ea.status_prata = 'concluido'
                      AND ea.fonte = 'concursos'
                      AND (ea.status_ouro IS NULL OR ea.status_ouro = 'erro')
                      AND e.modalidade = %s
                    ORDER BY ea.concurso_inicio
                    """,
                    (modalidade,),
                )
            else:
                cur.execute(
                    """
                    SELECT id_arquivo, caminho_arquivo, concurso_inicio, concurso_fim
                    FROM pipeline.execucao_arquivo
                    WHERE status_prata = 'concluido'
                      AND fonte = 'concursos'
                      AND (status_ouro IS NULL OR status_ouro = 'erro')
                    ORDER BY concurso_inicio
                    """
                )
            return list(cur.fetchall())


QUALIDADE_ESTRITA = os.environ.get("LOTERIAS_QUALIDADE_ESTRITA", "0") == "1"


class GoldLoader:
    def __init__(self) -> None:
        self.conn = emprestar()
        self.tipo_jogo: dict[str, int] = {}
        self.localidade: dict[tuple[str, str], int] = {}
        self.faixa: dict[tuple[int, int, int], int] = {}
        self.local_sorteio: dict[tuple[str, int], int] = {}
        self.concurso: dict[tuple[int, int], int] = {}
        self._hydrate_tipo_jogo()

    def close(self) -> None:
        devolver(self.conn)

    def _hydrate_tipo_jogo(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT id_tipo_jogo, codigo FROM public.tipo_jogo")
            for tid, codigo in cur.fetchall():
                self.tipo_jogo[str(codigo)] = int(tid)

    def _refresh_localidade_cache(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT id_localidade, municipio, uf FROM public.localidade")
            for lid, mun, uf in cur.fetchall():
                self.localidade[(str(mun), str(uf))] = int(lid)

    def _refresh_faixa_cache(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT id_faixa, id_tipo_jogo, numero_faixa, acertos FROM public.faixa")
            for fid, tid, nf, ac in cur.fetchall():
                self.faixa[(int(tid), int(nf), int(ac))] = int(fid)

    def _refresh_local_sorteio_cache(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT id_local_sorteio, nome, id_localidade FROM public.local_sorteio")
            for sid, nome, lid in cur.fetchall():
                self.local_sorteio[(str(nome), int(lid))] = int(sid)

    def bulk_tipo_jogo(self, df: pd.DataFrame) -> None:
        rows = [
            (
                int(r.get("id_tipo_jogo", 1)),
                r["codigo"],
                r["nome_exibicao"],
                int(r["dezenas_sorteadas"]),
                int(r["dezenas_apostadas"]),
                int(r["dezenas_disponiveis"]),
            )
            for _, r in df.iterrows()
        ]
        with self.conn.cursor() as cur:
            execute_batch(
                cur,
                """
                INSERT INTO public.tipo_jogo (
                    id_tipo_jogo, codigo, nome_exibicao,
                    dezenas_sorteadas, dezenas_apostadas, dezenas_disponiveis
                )
                OVERRIDING SYSTEM VALUE
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (codigo) DO NOTHING
                """,
                rows,
            )
        self.conn.commit()
        self._hydrate_tipo_jogo()

    def bulk_localidade(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        rows = [(str(r["municipio"]), str(r["uf"])) for _, r in df.iterrows()]
        rows = list(dict.fromkeys(rows))
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

    def bulk_faixa(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        rows = []
        for _, r in df.iterrows():
            tid = self.tipo_jogo[str(r["codigo_tipo_jogo"])]
            chave = (tid, int(r["numero_faixa"]), int(r["acertos"]))
            if chave in self.faixa:
                continue
            rows.append((chave[0], chave[1], r.get("descricao"), chave[2]))
        if not rows:
            return
        with self.conn.cursor() as cur:
            execute_batch(
                cur,
                """
                INSERT INTO public.faixa (id_tipo_jogo, numero_faixa, descricao, acertos)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id_tipo_jogo, numero_faixa, acertos) DO NOTHING
                """,
                rows,
            )
        self.conn.commit()
        self._refresh_faixa_cache()

    def bulk_local_sorteio(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        rows = []
        for _, r in df.iterrows():
            lid = self.localidade[(str(r["municipio"]), str(r["uf"]))]
            nome = str(r["nome"])
            if (nome, lid) in self.local_sorteio:
                continue
            rows.append((nome, lid))
        if not rows:
            return
        with self.conn.cursor() as cur:
            execute_batch(
                cur,
                """
                INSERT INTO public.local_sorteio (nome, id_localidade)
                VALUES (%s, %s)
                ON CONFLICT (nome, id_localidade) DO NOTHING
                """,
                rows,
            )
        self.conn.commit()
        self._refresh_local_sorteio_cache()

    def load_silver_dir(self, silver_dir: Path) -> None:
        log(f"carregando silver: {silver_dir.name}")
        validar_pasta_silver(silver_dir, estrito=QUALIDADE_ESTRITA)

        self.bulk_tipo_jogo(pd.read_parquet(silver_dir / "tipo_jogo.parquet"))

        loc_path = silver_dir / "localidade.parquet"
        if loc_path.exists():
            self.bulk_localidade(pd.read_parquet(loc_path))

        self.bulk_faixa(pd.read_parquet(silver_dir / "faixa.parquet"))
        self.bulk_local_sorteio(pd.read_parquet(silver_dir / "local_sorteio.parquet"))

        df_con = pd.read_parquet(silver_dir / "concurso.parquet")
        concurso_rows = []
        for _, r in df_con.iterrows():
            tid = self.tipo_jogo[str(r["codigo_tipo_jogo"])]
            id_local_sorteio = None
            nome_ls = r.get("local_sorteio_nome")
            mun = r.get("localidade_municipio")
            uf = r.get("localidade_uf")
            if pd.notna(nome_ls) and pd.notna(mun) and pd.notna(uf):
                lid = self.localidade[(str(mun), str(uf))]
                id_local_sorteio = self.local_sorteio.get((str(nome_ls), lid))
                if id_local_sorteio is None:
                    with self.conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO public.local_sorteio (nome, id_localidade)
                            VALUES (%s, %s)
                            ON CONFLICT (nome, id_localidade) DO UPDATE
                                SET nome = EXCLUDED.nome
                            RETURNING id_local_sorteio
                            """,
                            (str(nome_ls), lid),
                        )
                        id_local_sorteio = int(cur.fetchone()[0])
                    self.conn.commit()
                    self.local_sorteio[(str(nome_ls), lid)] = id_local_sorteio

            concurso_rows.append(
                (
                    tid,
                    int(r["numero_concurso"]),
                    r["data_apuracao"],
                    r.get("data_proximo_concurso"),
                    _int_or_none(r.get("numero_concurso_anterior")),
                    _int_or_none(r.get("numero_concurso_proximo")),
                    _int_or_none(r.get("numero_concurso_final_0_5")),
                    id_local_sorteio,
                    bool(r["acumulado"]),
                    bool(r["ultimo_concurso"]),
                    _int_or_none(r.get("indicador_concurso_especial")),
                    _int_or_none(r.get("tipo_publicacao")),
                    _int_or_none(r.get("numero_jogo")),
                    r.get("observacao"),
                    _num_or_none(r.get("valor_arrecadado")),
                    _num_or_none(r.get("valor_estimado_proximo_concurso")),
                    _num_or_none(r.get("valor_acumulado_proximo_concurso")),
                    _num_or_none(r.get("valor_acumulado_concurso_especial")),
                    _num_or_none(r.get("valor_acumulado_concurso_0_5")),
                    _num_or_none(r.get("valor_saldo_reserva_garantidora")),
                    _num_or_none(r.get("valor_total_premio_faixa_um")),
                )
            )

        with self.conn.cursor() as cur:
            execute_batch(
                cur,
                """
                INSERT INTO public.concurso (
                    id_tipo_jogo, numero_concurso, data_apuracao, data_proximo_concurso,
                    numero_concurso_anterior, numero_concurso_proximo, numero_concurso_final_0_5,
                    id_local_sorteio, acumulado, ultimo_concurso, indicador_concurso_especial,
                    tipo_publicacao, numero_jogo, observacao, valor_arrecadado,
                    valor_estimado_proximo_concurso, valor_acumulado_proximo_concurso,
                    valor_acumulado_concurso_especial, valor_acumulado_concurso_0_5,
                    valor_saldo_reserva_garantidora, valor_total_premio_faixa_um
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (id_tipo_jogo, numero_concurso) DO UPDATE SET
                    id_local_sorteio = COALESCE(
                        public.concurso.id_local_sorteio, EXCLUDED.id_local_sorteio
                    ),
                    valor_arrecadado = EXCLUDED.valor_arrecadado,
                    valor_estimado_proximo_concurso = EXCLUDED.valor_estimado_proximo_concurso,
                    valor_acumulado_proximo_concurso = EXCLUDED.valor_acumulado_proximo_concurso,
                    valor_acumulado_concurso_especial = EXCLUDED.valor_acumulado_concurso_especial,
                    valor_acumulado_concurso_0_5 = EXCLUDED.valor_acumulado_concurso_0_5,
                    valor_saldo_reserva_garantidora = EXCLUDED.valor_saldo_reserva_garantidora,
                    valor_total_premio_faixa_um = EXCLUDED.valor_total_premio_faixa_um
                """,
                concurso_rows,
            )
            nums = [int(r[1]) for r in concurso_rows]
            tid = concurso_rows[0][0] if concurso_rows else None
            if tid is not None:
                cur.execute(
                    """
                    SELECT numero_concurso, id_concurso
                    FROM public.concurso
                    WHERE id_tipo_jogo = %s AND numero_concurso = ANY(%s)
                    """,
                    (tid, nums),
                )
                for num, cid in cur.fetchall():
                    self.concurso[(tid, int(num))] = int(cid)
        self.conn.commit()

        df_dez = pd.read_parquet(silver_dir / "dezena.parquet")
        dez_rows = []
        for _, r in df_dez.iterrows():
            tid = self.tipo_jogo[str(r["codigo_tipo_jogo"])]
            cid = self.concurso[(tid, int(r["numero_concurso"]))]
            dez_rows.append(
                (
                    cid,
                    int(r["numero"]),
                    r.get("ordem_sorteio") if pd.notna(r.get("ordem_sorteio")) else None,
                    r.get("ordem_crescente") if pd.notna(r.get("ordem_crescente")) else None,
                    bool(r["segundo_sorteio"]),
                )
            )
        if dez_rows:
            with self.conn.cursor() as cur:
                execute_batch(
                    cur,
                    """
                    INSERT INTO public.dezena (
                        id_concurso, numero, ordem_sorteio, ordem_crescente, segundo_sorteio
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (id_concurso, numero, segundo_sorteio) DO NOTHING
                    """,
                    dez_rows,
                )
            self.conn.commit()

        df_rat = pd.read_parquet(silver_dir / "rateio.parquet")
        rat_rows = []
        for _, r in df_rat.iterrows():
            tid = self.tipo_jogo[str(r["codigo_tipo_jogo"])]
            cid = self.concurso[(tid, int(r["numero_concurso"]))]
            fid = self.faixa[(tid, int(r["numero_faixa"]), int(r["acertos"]))]
            rat_rows.append(
                (
                    cid,
                    fid,
                    r.get("numero_ganhadores"),
                    _num_or_none(r.get("valor_premio")),
                    _num_or_none(r.get("valor_total")),
                )
            )
        if rat_rows:
            with self.conn.cursor() as cur:
                execute_batch(
                    cur,
                    """
                    INSERT INTO public.rateio (
                        id_concurso, id_faixa, numero_ganhadores, valor_premio, valor_total
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (id_concurso, id_faixa) DO UPDATE SET
                        valor_premio = EXCLUDED.valor_premio,
                        valor_total = EXCLUDED.valor_total
                    """,
                    rat_rows,
                )
            self.conn.commit()

        path_gm = silver_dir / "ganhador_municipio.parquet"
        if path_gm.exists():
            df_gm = pd.read_parquet(path_gm)
            if not df_gm.empty:
                gm_rows = []
                for _, r in df_gm.iterrows():
                    tid = self.tipo_jogo[str(r["codigo_tipo_jogo"])]
                    cid = self.concurso[(tid, int(r["numero_concurso"]))]
                    lid = self.localidade[(str(r["municipio"]), str(r["uf"]))]
                    gm_rows.append(
                        (
                            cid,
                            lid,
                            r.get("numero_ganhadores"),
                            r.get("posicao"),
                            r.get("nome_fantasia_ul"),
                            r.get("serie"),
                        )
                    )
                with self.conn.cursor() as cur:
                    execute_batch(
                        cur,
                        """
                        INSERT INTO public.ganhador_municipio (
                            id_concurso, id_localidade, numero_ganhadores,
                            posicao, nome_fantasia_ul, serie
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id_concurso, id_localidade, posicao) DO NOTHING
                        """,
                        gm_rows,
                    )
                self.conn.commit()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Silver concursos -> Gold (PostgreSQL).")
    p.add_argument(
        "--modalidade", default=None, help="Opcional: filtrar por modalidade (ex: MEGA_SENA)"
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    set_context(args.modalidade or "TODOS", "gold.concursos")

    orfaos = reset_ouro_processando_orfaos("concursos")
    if orfaos:
        log(f"resetados {orfaos} arquivo(s) 'processando' orfaos -> NULL")

    arquivos = fetch_arquivos_prata_concluidos(args.modalidade)
    if not arquivos:
        log("nenhum arquivo com status_prata=concluido (e ouro pendente)")
        return 0

    secao(f"GOLD CONCURSOS - {len(arquivos)} arquivo(s) -> PostgreSQL")
    loader = GoldLoader()
    erros = 0
    try:
        for id_arquivo, caminho, ci, cf in arquivos:
            secao(f"gold arquivo id={id_arquivo} concursos {ci}-{cf}")
            update_status_ouro(id_arquivo, "processando")
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
                evento("gold_ok", id_arquivo=id_arquivo, concursos=f"{ci}-{cf}")
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
                    aviso("loader gold.concursos com conexao morta - recriando")
                    try:
                        loader.close()
                    except Exception:
                        pass
                    loader = GoldLoader()
    finally:
        loader.close()

    with conexao() as conn:
        with conn.cursor() as cur:
            completadas = data_proximo.completar(cur)
            normalizados = normalizacao.normalizar(cur)
        conn.commit()
    evento("data_proximo_completada", concursos=completadas)
    evento("normalizacao", **vars(normalizados))

    secao("RESUMO")
    evento("fim", sucesso=len(arquivos) - erros, erros=erros)
    return 1 if erros else 0


if __name__ == "__main__":
    raise SystemExit(main())
