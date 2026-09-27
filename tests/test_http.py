from __future__ import annotations

from importlib.metadata import version

from loterias import http
from loterias.bronze.concursos import DEFAULT_HEADERS as cabecalhos_concursos
from loterias.bronze.locais_sorte import DEFAULT_HEADERS as cabecalhos_locais


def test_user_agent_identifica_o_projeto_e_o_repositorio():
    agente = http.user_agent()
    assert http.NOME_PROJETO in agente
    assert http.REPOSITORIO in agente


def test_user_agent_nao_finge_ser_navegador():
    agente = http.user_agent().lower()
    for disfarce in ("chrome", "firefox", "safari/", "edg/", "windows nt"):
        assert disfarce not in agente, f"o agente esta imitando navegador: {disfarce}"


def test_versao_do_agente_acompanha_o_pacote():
    assert f"/{version('loterias')};" in http.user_agent()


def test_as_duas_fontes_usam_o_mesmo_agente():
    assert cabecalhos_concursos["User-Agent"] == cabecalhos_locais["User-Agent"]
    assert cabecalhos_concursos["User-Agent"] == http.user_agent()


def test_locais_sorte_declara_a_origem_do_portal():
    for chave, valor in http.PORTAL_LOTERIAS.items():
        assert cabecalhos_locais[chave] == valor


def test_concursos_nao_carrega_cabecalho_de_portal():
    assert "Referer" not in cabecalhos_concursos
    assert "Origin" not in cabecalhos_concursos


def test_extras_sobrescrevem_a_base_sem_perder_o_agente():
    montado = http.cabecalhos({"Accept": "text/csv", "X-Teste": "1"})
    assert montado["Accept"] == "text/csv"
    assert montado["X-Teste"] == "1"
    assert montado["User-Agent"] == http.user_agent()
