from __future__ import annotations

from datetime import date

import pytest

from loterias.gold import data_proximo
from loterias.silver.concursos import preencher_data_proximo_concurso_historico


def _payload(tipo: str, numero: int, apuracao: str) -> dict:
    return {
        "tipoJogo": tipo,
        "numero": numero,
        "dataApuracao": apuracao,
        "dataProximoConcurso": "",
    }


def test_preenchimento_do_silver_nao_cruza_modalidades():
    payloads = [
        _payload("QUINA", 1, "13/03/1994"),
        _payload("LOTOMANIA", 1, "02/10/1999"),
        _payload("QUINA", 2, "17/03/1994"),
        _payload("LOTOMANIA", 2, "09/10/1999"),
    ]

    assert preencher_data_proximo_concurso_historico(payloads) == 2

    proximo = {(p["tipoJogo"], p["numero"]): p["dataProximoConcurso"] for p in payloads}
    assert proximo[("QUINA", 1)] == "17/03/1994"
    assert proximo[("LOTOMANIA", 1)] == "09/10/1999"
    assert proximo[("QUINA", 2)] == ""
    assert proximo[("LOTOMANIA", 2)] == ""


@pytest.mark.integracao
def test_gold_completa_data_ausente_ou_impossivel_e_preserva_a_previsao(conexao_banco):
    with conexao_banco.cursor() as cur:
        cur.execute("SELECT MIN(id_tipo_jogo) FROM public.tipo_jogo")
        id_tipo_jogo = cur.fetchone()[0]
        if id_tipo_jogo is None:
            pytest.skip("tipo_jogo vazio")

        casos = [
            (900001, date(2000, 1, 1), None),
            (900002, date(2000, 1, 5), date(2000, 1, 1)),
            (900003, date(2000, 1, 8), date(2000, 1, 12)),
            (900004, date(2000, 1, 13), date(2000, 1, 13)),
            (900005, date(2000, 1, 13), None),
        ]
        for numero, apuracao, previsto in casos:
            cur.execute(
                """
                INSERT INTO public.concurso
                    (id_tipo_jogo, numero_concurso, data_apuracao, data_proximo_concurso)
                VALUES (%s, %s, %s, %s)
                """,
                (id_tipo_jogo, numero, apuracao, previsto),
            )

        data_proximo.completar(cur)

        cur.execute(
            """
            SELECT numero_concurso, data_proximo_concurso FROM public.concurso
            WHERE id_tipo_jogo = %s AND numero_concurso > 900000
            """,
            (id_tipo_jogo,),
        )
        assert dict(cur.fetchall()) == {
            900001: date(2000, 1, 5),
            900002: date(2000, 1, 8),
            900003: date(2000, 1, 12),
            900004: date(2000, 1, 13),
            900005: None,
        }
    conexao_banco.rollback()


@pytest.mark.integracao
def test_nenhum_concurso_fica_com_data_do_proximo_reparavel(conexao_banco):
    with conexao_banco.cursor() as cur:
        assert data_proximo.contar_reparaveis(cur) == 0
