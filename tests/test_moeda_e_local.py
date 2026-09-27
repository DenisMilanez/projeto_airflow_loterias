from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from loterias.silver.concursos import transform_payloads
from loterias.silver.moeda import em_reais


def _payload(data: str, local: str = "", arrecadado: float = 0.0) -> dict:
    return {
        "numero": 25,
        "tipoJogo": "QUINA",
        "dataApuracao": data,
        "dataProximoConcurso": "",
        "nomeMunicipioUFSorteio": "SÃO PAULO, SP",
        "localSorteio": local,
        "valorArrecadado": arrecadado,
        "valorEstimadoProximoConcurso": 27_500_000.0,
        "acumulado": False,
        "ultimoConcurso": False,
        "listaDezenas": ["01", "02", "03", "04", "05"],
        "dezenasSorteadasOrdemSorteio": ["05", "04", "03", "02", "01"],
        "listaMunicipioUFGanhadores": [],
        "listaRateioPremio": [
            {
                "faixa": 1,
                "descricaoFaixa": "5 acertos",
                "numeroDeGanhadores": 2,
                "valorPremio": 579_215_957.0,
            }
        ],
    }


@pytest.mark.parametrize(
    ("valor", "data", "esperado"),
    [
        (2750.0, date(1994, 6, 30), 1.0),
        (579_215_957.0, date(1994, 6, 16), 210_623.98),
        (2750.0, date(1994, 7, 1), 2750.0),
        (None, date(1994, 6, 16), None),
        (2750.0, None, 2750.0),
    ],
)
def test_valor_anterior_ao_real_vira_reais(valor, data, esperado):
    assert em_reais(valor, data) == esperado


def test_silver_converte_premio_e_estimativa_de_1994():
    silver = transform_payloads([_payload("16/06/1994")])

    rateio = silver["rateio"].iloc[0]
    assert rateio["valor_premio"] == 210_623.98
    assert rateio["valor_total"] == 421_247.97
    assert silver["concurso"].iloc[0]["valor_estimado_proximo_concurso"] == 10_000.0


def test_silver_nao_mexe_em_valor_depois_do_real():
    silver = transform_payloads([_payload("16/06/2026", arrecadado=10_000.0)])

    assert silver["rateio"].iloc[0]["valor_premio"] == 579_215_957.0
    assert silver["concurso"].iloc[0]["valor_arrecadado"] == 10_000.0


def test_arrecadacao_zero_vira_ausente():
    silver = transform_payloads([_payload("16/06/2026", arrecadado=0.0)])

    assert pd.isna(silver["concurso"].iloc[0]["valor_arrecadado"])


def test_local_vazio_preserva_a_cidade_do_sorteio():
    silver = transform_payloads([_payload("16/06/2026", local="")])

    assert silver["local_sorteio"].to_dict("records") == [
        {"nome": "NAO INFORMADO", "municipio": "SAO PAULO", "uf": "SP"}
    ]
    assert silver["concurso"].iloc[0]["local_sorteio_nome"] == "NAO INFORMADO"


def test_local_invalido_tambem_preserva_a_cidade():
    silver = transform_payloads([_payload("16/06/2026", local="QUINA")])

    assert silver["concurso"].iloc[0]["local_sorteio_nome"] == "NAO INFORMADO"


@pytest.mark.integracao
def test_banco_nao_guarda_cruzeiro_nem_arrecadacao_zero(conexao_banco):
    with conexao_banco.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(MAX(r.valor_premio), 0)
            FROM public.rateio r JOIN public.concurso c ON c.id_concurso = r.id_concurso
            WHERE c.data_apuracao < '1994-07-01'
            """
        )
        assert cur.fetchone()[0] < 1_000_000
        cur.execute("SELECT COUNT(*) FROM public.concurso WHERE valor_arrecadado = 0")
        assert cur.fetchone()[0] == 0
