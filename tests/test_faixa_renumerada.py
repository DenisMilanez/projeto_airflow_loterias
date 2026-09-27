from __future__ import annotations

import pytest

from loterias.silver.concursos import transform_payloads


def _concurso(numero: int, data: str, faixas: list[tuple[int, str, int]]) -> dict:
    return {
        "numero": numero,
        "tipoJogo": "LOTOMANIA",
        "dataApuracao": data,
        "listaRateioPremio": [
            {"faixa": f, "descricaoFaixa": d, "numeroDeGanhadores": g, "valorPremio": 10.0}
            for f, d, g in faixas
        ],
    }


def test_faixa_renumerada_vira_duas_faixas_no_silver():
    antes = _concurso(1652, "01/03/2016", [(6, "0 acertos", 1)])
    depois = _concurso(1653, "04/03/2016", [(6, "15 acertos", 9000), (7, "0 acertos", 1)])

    silver = transform_payloads([antes, depois])

    faixas = sorted(silver["faixa"][["numero_faixa", "acertos"]].itertuples(index=False, name=None))
    assert faixas == [(6, 0), (6, 15), (7, 0)]
    colunas = ["numero_concurso", "numero_faixa", "acertos"]
    rateio = sorted(silver["rateio"][colunas].itertuples(index=False, name=None))
    assert rateio == [(1652, 6, 0), (1653, 6, 15), (1653, 7, 0)]


@pytest.mark.integracao
def test_nenhum_concurso_tem_duas_faixas_com_os_mesmos_acertos(conexao_banco):
    with conexao_banco.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT r.id_concurso, f.acertos
                FROM public.rateio r JOIN public.faixa f ON f.id_faixa = r.id_faixa
                GROUP BY 1, 2
                HAVING COUNT(*) > 1
            ) repetidas
            """
        )
        assert cur.fetchone()[0] == 0
