from __future__ import annotations

import pytest

from loterias import caminhos

RELATIVO = "data/bronze/quina/concursos/2026/09/13/quina_concursos_7101_7120_20260913.parquet"


def test_registro_guarda_caminho_relativo_a_raiz():
    arquivo = caminhos.raiz() / RELATIVO
    assert caminhos.registrar(arquivo) == RELATIVO


@pytest.mark.parametrize(
    "registrado",
    [
        RELATIVO,
        "/opt/loterias/" + RELATIVO,
        "D:\\projetos\\projeto_airflow_loterias\\" + RELATIVO.replace("/", "\\"),
    ],
)
def test_caminho_registrado_em_qualquer_maquina_resolve_aqui(registrado):
    assert caminhos.localizar(registrado) == caminhos.raiz() / RELATIVO
