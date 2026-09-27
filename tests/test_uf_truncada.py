from __future__ import annotations

import pytest

from loterias.silver.concursos import parse_municipio_uf
from loterias.silver.ibge import lookup_uf
from loterias.silver.locais_sorte import parse_cidade_uf


@pytest.mark.parametrize(
    ("municipio", "prefixo", "esperado"),
    [
        ("Santa Helena de Goias", "G", "GO"),
        ("Fortaleza", "C", "CE"),
        ("Sao Paulo", None, "SP"),
    ],
)
def test_lookup_resolve_uf_truncada(municipio, prefixo, esperado):
    assert lookup_uf(municipio, prefixo) == esperado


@pytest.mark.parametrize("prefixo", [None, "P"])
def test_lookup_devolve_none_para_cidade_homonima(prefixo):
    assert lookup_uf("Bom Jesus", prefixo) is None


def test_lookup_devolve_none_para_cidade_inexistente():
    assert lookup_uf("Cidade Que Nao Existe", None) is None


def test_concursos_2030_uf_g_vira_go():
    assert parse_municipio_uf("SANTA HELENA DE GOIAS, G") == ("SANTA HELENA DE GOIAS", "GO")


def test_concursos_2034_uf_c_vira_ce():
    assert parse_municipio_uf("FORTALEZA, C") == ("FORTALEZA", "CE")


def test_concursos_uf_ambigua_nao_e_propagada():
    assert parse_municipio_uf("BOM JESUS, P") is None


def test_locais_sorte_uf_truncada_vira_uf_completa():
    assert parse_cidade_uf("SANTA HELENA DE GOIAS/G") == ("SANTA HELENA DE GOIAS", "GO")


def test_locais_sorte_uf_ambigua_cai_no_fallback_seguro():
    assert parse_cidade_uf("BOM JESUS/P") == ("NAO INFORMADO", "NA")


def test_locais_sorte_ignora_loteria_digital():
    assert parse_cidade_uf("LOTERIA DIGITAL/X") == ("NAO INFORMADO", "NA")


def test_uf_valida_de_dois_chars_passa_direto():
    assert parse_cidade_uf("CURITIBA/PR") == ("CURITIBA", "PR")
    assert parse_municipio_uf("CURITIBA, PR") == ("CURITIBA", "PR")
