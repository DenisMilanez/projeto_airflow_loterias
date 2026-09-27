from __future__ import annotations

import sys

from loterias.db import connect

PUBLIC_TABLES: list[tuple[str, str]] = [
    (
        "tipo_jogo",
        """
        CREATE TABLE tipo_jogo (
            id_tipo_jogo SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            codigo VARCHAR(30) NOT NULL UNIQUE,
            nome_exibicao VARCHAR(60) NOT NULL,
            dezenas_sorteadas SMALLINT NOT NULL,
            dezenas_apostadas SMALLINT NOT NULL,
            dezenas_disponiveis SMALLINT NOT NULL
        );
        """,
    ),
    (
        "faixa",
        """
        CREATE TABLE faixa (
            id_faixa SERIAL PRIMARY KEY,
            id_tipo_jogo SMALLINT NOT NULL REFERENCES tipo_jogo (id_tipo_jogo),
            numero_faixa SMALLINT NOT NULL,
            descricao VARCHAR(120),
            acertos SMALLINT NOT NULL,
            CONSTRAINT uq_faixa_tipo_numero_acertos UNIQUE (id_tipo_jogo, numero_faixa, acertos)
        );
        """,
    ),
    (
        "localidade",
        """
        CREATE TABLE localidade (
            id_localidade SERIAL PRIMARY KEY,
            municipio VARCHAR(120) NOT NULL,
            uf CHAR(2) NOT NULL,
            CONSTRAINT uq_localidade_municipio_uf UNIQUE (municipio, uf)
        );
        """,
    ),
    (
        "local_sorteio",
        """
        CREATE TABLE local_sorteio (
            id_local_sorteio SERIAL PRIMARY KEY,
            nome VARCHAR(120) NOT NULL,
            id_localidade INT NOT NULL REFERENCES localidade (id_localidade),
            CONSTRAINT uq_local_sorteio_nome_localidade UNIQUE (nome, id_localidade)
        );
        """,
    ),
    (
        "concurso",
        """
        CREATE TABLE concurso (
            id_concurso SERIAL PRIMARY KEY,
            id_tipo_jogo SMALLINT NOT NULL REFERENCES tipo_jogo (id_tipo_jogo),
            numero_concurso INT NOT NULL,
            data_apuracao DATE NOT NULL,
            data_proximo_concurso DATE,
            numero_concurso_anterior INT,
            numero_concurso_proximo INT,
            numero_concurso_final_0_5 INT,
            id_local_sorteio INT REFERENCES local_sorteio (id_local_sorteio),
            acumulado BOOLEAN NOT NULL DEFAULT FALSE,
            ultimo_concurso BOOLEAN NOT NULL DEFAULT FALSE,
            indicador_concurso_especial SMALLINT,
            tipo_publicacao SMALLINT,
            numero_jogo SMALLINT,
            observacao TEXT,
            valor_arrecadado NUMERIC(18, 2),
            valor_estimado_proximo_concurso NUMERIC(18, 2),
            valor_acumulado_proximo_concurso NUMERIC(18, 2),
            valor_acumulado_concurso_especial NUMERIC(18, 2),
            valor_acumulado_concurso_0_5 NUMERIC(18, 2),
            valor_saldo_reserva_garantidora NUMERIC(18, 2),
            valor_total_premio_faixa_um NUMERIC(18, 2),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_concurso UNIQUE (id_tipo_jogo, numero_concurso)
        );
        """,
    ),
    (
        "dezena",
        """
        CREATE TABLE dezena (
            id_dezena SERIAL PRIMARY KEY,
            id_concurso INT NOT NULL REFERENCES concurso (id_concurso) ON DELETE CASCADE,
            numero SMALLINT NOT NULL,
            ordem_sorteio SMALLINT,
            ordem_crescente SMALLINT,
            segundo_sorteio BOOLEAN NOT NULL DEFAULT FALSE,
            CONSTRAINT uq_dezena_concurso_numero_segundo
                UNIQUE (id_concurso, numero, segundo_sorteio)
        );
        """,
    ),
    (
        "rateio",
        """
        CREATE TABLE rateio (
            id_rateio SERIAL PRIMARY KEY,
            id_concurso INT NOT NULL REFERENCES concurso (id_concurso) ON DELETE CASCADE,
            id_faixa INT NOT NULL REFERENCES faixa (id_faixa),
            numero_ganhadores INT,
            valor_premio NUMERIC(18, 2),
            valor_total NUMERIC(18, 2),
            CONSTRAINT uq_rateio_concurso_faixa UNIQUE (id_concurso, id_faixa)
        );
        """,
    ),
    (
        "ganhador_municipio",
        """
        CREATE TABLE ganhador_municipio (
            id_ganhador_municipio SERIAL PRIMARY KEY,
            id_concurso INT NOT NULL REFERENCES concurso (id_concurso) ON DELETE CASCADE,
            id_localidade INT NOT NULL REFERENCES localidade (id_localidade),
            numero_ganhadores INT,
            posicao SMALLINT,
            nome_fantasia_ul VARCHAR(120),
            serie VARCHAR(30),
            CONSTRAINT uq_ganhador_municipio
                UNIQUE NULLS NOT DISTINCT (id_concurso, id_localidade, posicao)
        );
        """,
    ),
    (
        "loterica",
        """
        CREATE TABLE loterica (
            id_loterica   SERIAL PRIMARY KEY,
            razao_social  VARCHAR(120) NOT NULL,
            nome_fantasia VARCHAR(120),
            id_localidade INT REFERENCES localidade (id_localidade),
            canal_vendas  VARCHAR(20) NOT NULL DEFAULT 'Fisico',
            CONSTRAINT uq_loterica
                UNIQUE NULLS NOT DISTINCT (razao_social, canal_vendas, id_localidade)
        );
        """,
    ),
    (
        "ganhador_loterica",
        """
        CREATE TABLE ganhador_loterica (
            id_ganhador_loterica         SERIAL PRIMARY KEY,
            id_concurso                  INT NOT NULL REFERENCES concurso (id_concurso) ON DELETE CASCADE,
            id_loterica                  INT NOT NULL REFERENCES loterica (id_loterica),
            id_faixa                     INT NOT NULL REFERENCES faixa (id_faixa),
            canal_vendas                 VARCHAR(30),
            tipo_aposta                  VARCHAR(30),
            numero_cotas                 SMALLINT,
            quantidade_numeros_apostados SMALLINT,
            quantidade_premios_por_faixa INT,
            premio_total                 NUMERIC(18, 2),
            teimosinha                   BOOLEAN,
            CONSTRAINT uq_ganhador_loterica
                UNIQUE NULLS NOT DISTINCT (id_concurso, id_loterica, id_faixa, tipo_aposta)
        );
        """,
    ),
]


AJUSTES_EM_BANCO_EXISTENTE = [
    "ALTER TABLE faixa DROP CONSTRAINT IF EXISTS uq_faixa_tipo_numero",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_faixa_tipo_numero_acertos') THEN
            ALTER TABLE faixa ADD CONSTRAINT uq_faixa_tipo_numero_acertos
                UNIQUE (id_tipo_jogo, numero_faixa, acertos);
        END IF;
    END
    $$
    """,
]


def table_exists(cur, name: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (name,),
    )
    return cur.fetchone() is not None


def postgres_major_version(cur) -> int:
    cur.execute("SHOW server_version_num")
    raw = cur.fetchone()[0]
    return int(raw) // 10000


def main() -> int:
    try:
        conn = connect()
    except Exception as e:
        print(f"Falha na conexao: {e}", file=sys.stderr)
        return 1

    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            pg_major = postgres_major_version(cur)
            print(f"PostgreSQL major version: {pg_major}")
            for name, ddl in PUBLIC_TABLES:
                if table_exists(cur, name):
                    print(f"public.{name} ja existe - nao recriando.")
                    continue
                ddl_final = ddl
                if pg_major < 15 and "NULLS NOT DISTINCT" in ddl_final:
                    ddl_final = ddl_final.replace(" NULLS NOT DISTINCT", "")
                    print(f"  (PG<15: removendo NULLS NOT DISTINCT em {name})")
                print(f"Criando public.{name} ...")
                cur.execute(ddl_final)
            for ajuste in AJUSTES_EM_BANCO_EXISTENTE:
                cur.execute(ajuste)

        conn.commit()
        print("Concluido (commit).")
    except Exception as e:
        conn.rollback()
        print(f"Erro ao criar tabelas (rollback): {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
