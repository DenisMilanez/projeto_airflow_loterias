from __future__ import annotations

import sys

from loterias.config import _jogo, listar_modalidades
from loterias.db import connect


def print_rows(title: str, rows: list[tuple], colnames: list[str]) -> None:
    print(f"\n--- {title} ---")
    if not rows:
        print("(sem linhas)")
        return
    print(" | ".join(colnames))
    print("-" * (sum(len(c) for c in colnames) + 3 * (len(colnames) - 1)))
    for row in rows:
        print(" | ".join(str(x) for x in row))


DDL_EXECUCAO = """
CREATE TABLE IF NOT EXISTS pipeline.execucao (
    id_execucao SERIAL PRIMARY KEY,
    modalidade VARCHAR(20) NOT NULL,
    tipo VARCHAR(20) NOT NULL,
    concurso_inicio INT,
    concurso_fim INT,
    total_coletados INT,
    status VARCHAR(20) NOT NULL,
    mensagem_erro TEXT,
    iniciado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finalizado_em TIMESTAMPTZ
);
"""

DDL_EXECUCAO_ARQUIVO = """
CREATE TABLE IF NOT EXISTS pipeline.execucao_arquivo (
    id_arquivo SERIAL PRIMARY KEY,
    id_execucao INT NOT NULL REFERENCES pipeline.execucao (id_execucao),
    caminho_arquivo TEXT NOT NULL,
    concurso_inicio INT NOT NULL,
    concurso_fim INT NOT NULL,
    total_registros INT NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status_prata VARCHAR(20) NOT NULL DEFAULT 'pendente',
    processado_em TIMESTAMPTZ,
    erro_prata TEXT
);
"""

DDL_ALTER_EXECUCAO = """
ALTER TABLE pipeline.execucao
    ADD COLUMN IF NOT EXISTS fonte VARCHAR(30) NOT NULL DEFAULT 'concursos',
    ADD COLUMN IF NOT EXISTS total_erros INT,
    ADD COLUMN IF NOT EXISTS total_sem_dados INT,
    ADD COLUMN IF NOT EXISTS concurso_esperado INT;
"""

DDL_ALTER_EXECUCAO_ARQUIVO = """
ALTER TABLE pipeline.execucao_arquivo
    ADD COLUMN IF NOT EXISTS fonte        VARCHAR(30) NOT NULL DEFAULT 'concursos',
    ADD COLUMN IF NOT EXISTS status_ouro  VARCHAR(20),
    ADD COLUMN IF NOT EXISTS erro_ouro    TEXT,
    ADD COLUMN IF NOT EXISTS processado_ouro_em TIMESTAMPTZ;
"""

DDL_LOCAIS_SORTE_FILA = """
CREATE TABLE IF NOT EXISTS pipeline.locais_sorte_fila (
    id                   SERIAL PRIMARY KEY,
    modalidade           VARCHAR(30)  NOT NULL,
    numero_concurso      INT          NOT NULL,
    status               VARCHAR(20)  NOT NULL DEFAULT 'pendente',
    tentativas           SMALLINT     NOT NULL DEFAULT 0,
    max_tentativas       SMALLINT     NOT NULL DEFAULT 10,
    ultima_tentativa_em  TIMESTAMPTZ,
    proximo_retry_em     TIMESTAMPTZ,
    erro_detalhe         TEXT,
    id_arquivo           INT REFERENCES pipeline.execucao_arquivo(id_arquivo),
    criado_em            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    atualizado_em        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_locais_fila UNIQUE (modalidade, numero_concurso)
);
"""

DDL_CONCURSO_FILA = """
CREATE TABLE IF NOT EXISTS pipeline.concurso_fila (
    id                   SERIAL       PRIMARY KEY,
    modalidade           VARCHAR(30)  NOT NULL,
    numero_concurso      INT          NOT NULL,
    status               VARCHAR(20)  NOT NULL DEFAULT 'pendente',
    tentativas           SMALLINT     NOT NULL DEFAULT 0,
    max_tentativas       SMALLINT     NOT NULL DEFAULT 10,
    ultima_tentativa_em  TIMESTAMPTZ,
    proximo_retry_em     TIMESTAMPTZ,
    erro_detalhe         TEXT,
    id_execucao          INT          REFERENCES pipeline.execucao(id_execucao),
    criado_em            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    atualizado_em        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_concurso_fila UNIQUE (modalidade, numero_concurso)
);
"""

DDL_ALTER_FILA_MAX_TENT = """
ALTER TABLE pipeline.concurso_fila
    ALTER COLUMN max_tentativas SET DEFAULT 10;
ALTER TABLE pipeline.locais_sorte_fila
    ALTER COLUMN max_tentativas SET DEFAULT 10;
UPDATE pipeline.concurso_fila SET max_tentativas = 10 WHERE max_tentativas < 10;
UPDATE pipeline.locais_sorte_fila SET max_tentativas = 10 WHERE max_tentativas < 10;
"""

DDL_SAUDE_DIARIA = """
CREATE TABLE IF NOT EXISTS pipeline.saude_diaria (
    data                 DATE         NOT NULL,
    modalidade           VARCHAR(30)  NOT NULL,
    fonte                VARCHAR(30)  NOT NULL,
    concursos_esperados  INT,
    concursos_obtidos    INT,
    ultimo_concurso_db   INT,
    atraso_dias          INT,
    pendentes_na_fila    INT,
    erros_na_fila        INT,
    abandonados_na_fila  INT,
    atualizado_em        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_saude_diaria PRIMARY KEY (data, modalidade, fonte)
);
"""


def seed_tipo_jogo_from_yaml(cur) -> int:
    rows = []
    for codigo in listar_modalidades():
        j = _jogo(codigo)
        rows.append(
            (
                int(j["id_tipo_jogo"]),
                codigo,
                str(j["nome_exibicao"]),
                int(j["dezenas_sorteadas"]),
                int(j["dezenas_apostadas"]),
                int(j["dezenas_disponiveis"]),
            )
        )
    inseridos = 0
    for row in rows:
        cur.execute(
            """
            INSERT INTO public.tipo_jogo (
                id_tipo_jogo, codigo, nome_exibicao,
                dezenas_sorteadas, dezenas_apostadas, dezenas_disponiveis
            )
            OVERRIDING SYSTEM VALUE
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (codigo) DO NOTHING
            """,
            row,
        )
        inseridos += cur.rowcount
    return inseridos


def main() -> int:
    try:
        conn = connect()
    except Exception as e:
        print(f"Falha na conexao: {e}", file=sys.stderr)
        return 1

    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute("CREATE SCHEMA IF NOT EXISTS pipeline")
        cur.execute(DDL_EXECUCAO)
        cur.execute(DDL_EXECUCAO_ARQUIVO)
        cur.execute(DDL_ALTER_EXECUCAO)
        cur.execute(DDL_ALTER_EXECUCAO_ARQUIVO)
        cur.execute(DDL_CONCURSO_FILA)
        cur.execute(DDL_LOCAIS_SORTE_FILA)
        cur.execute(DDL_ALTER_FILA_MAX_TENT)
        cur.execute(DDL_SAUDE_DIARIA)

        seed_count = seed_tipo_jogo_from_yaml(cur)
        print(f"Seed tipo_jogo: {seed_count} novo(s) jogo(s) inserido(s).")

        cur.execute(
            """
            SELECT schema_name FROM information_schema.schemata
            WHERE schema_name IN ('public', 'pipeline') ORDER BY schema_name
            """
        )
        print_rows("Schemas", cur.fetchall(), ["schema_name"])

        cur.execute(
            """
            SELECT table_schema, table_name FROM information_schema.tables
            WHERE table_schema IN ('public', 'pipeline')
            ORDER BY table_schema, table_name
            """
        )
        print_rows("Tabelas", cur.fetchall(), ["schema", "table"])

        cur.execute(
            "SELECT id_tipo_jogo, codigo, nome_exibicao FROM public.tipo_jogo ORDER BY id_tipo_jogo"
        )
        print_rows("tipo_jogo seed", cur.fetchall(), ["id", "codigo", "nome"])

        conn.commit()
        print("\nTransacao concluida com commit.")
    except Exception as e:
        conn.rollback()
        print(f"\nErro - rollback: {e}", file=sys.stderr)
        return 1
    finally:
        cur.close()
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
