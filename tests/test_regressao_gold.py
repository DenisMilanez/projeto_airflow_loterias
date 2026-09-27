from __future__ import annotations

import pytest

pytestmark = pytest.mark.integracao


def _localidade(cur, municipio: str, uf: str) -> int:
    cur.execute(
        """
        INSERT INTO public.localidade (municipio, uf) VALUES (%s, %s)
        ON CONFLICT (municipio, uf) DO UPDATE SET municipio = EXCLUDED.municipio
        RETURNING id_localidade
        """,
        (municipio, uf),
    )
    return cur.fetchone()[0]


def test_local_sorteio_repetido_entre_modalidades_nao_quebra(conexao_banco):
    with conexao_banco.cursor() as cur:
        id_localidade = _localidade(cur, "TESTE LOCAL SORTEIO", "SP")
        for _ in range(2):
            cur.execute(
                """
                INSERT INTO public.local_sorteio (nome, id_localidade) VALUES (%s, %s)
                ON CONFLICT (nome, id_localidade) DO NOTHING
                """,
                ("CAMINHAO DA SORTE TESTE", id_localidade),
            )
        cur.execute(
            "SELECT COUNT(*) FROM public.local_sorteio WHERE nome = %s",
            ("CAMINHAO DA SORTE TESTE",),
        )
        assert cur.fetchone()[0] == 1
    conexao_banco.rollback()


def test_faixas_com_mesmos_acertos_convivem(conexao_banco):
    with conexao_banco.cursor() as cur:
        cur.execute("SELECT id_tipo_jogo FROM public.tipo_jogo ORDER BY id_tipo_jogo LIMIT 1")
        id_tipo_jogo = cur.fetchone()[0]
        for numero_faixa in (91, 92):
            cur.execute(
                """
                INSERT INTO public.faixa (id_tipo_jogo, numero_faixa, acertos, descricao)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id_tipo_jogo, numero_faixa) DO NOTHING
                """,
                (id_tipo_jogo, numero_faixa, 6, f"teste faixa {numero_faixa}"),
            )
        cur.execute(
            """
            SELECT COUNT(*) FROM public.faixa
            WHERE id_tipo_jogo = %s AND acertos = 6 AND numero_faixa IN (91, 92)
            """,
            (id_tipo_jogo,),
        )
        assert cur.fetchone()[0] == 2
    conexao_banco.rollback()


def test_constraint_de_acertos_unicos_nao_existe(conexao_banco):
    with conexao_banco.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM pg_constraint
            WHERE conrelid = 'public.faixa'::regclass AND conname = 'uq_faixa_tipo_acertos'
            """
        )
        assert cur.fetchone()[0] == 0


def test_update_da_fila_usa_any_em_uma_query_so(conexao_banco):
    numeros = list(range(900001, 900051))
    with conexao_banco.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO pipeline.concurso_fila (modalidade, numero_concurso, status)
            VALUES (%s, %s, 'pendente')
            ON CONFLICT (modalidade, numero_concurso) DO NOTHING
            """,
            [("TESTE_BATCH", n) for n in numeros],
        )
        cur.execute(
            """
            UPDATE pipeline.concurso_fila
            SET status = 'concluido', tentativas = tentativas + 1, atualizado_em = NOW()
            WHERE modalidade = %s AND numero_concurso = ANY(%s)
            """,
            ("TESTE_BATCH", numeros),
        )
        assert cur.rowcount == len(numeros)
    conexao_banco.rollback()
