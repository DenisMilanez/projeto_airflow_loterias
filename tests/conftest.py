from __future__ import annotations

import pytest

from loterias.silver import ibge

INDICE_FALSO = {
    "SANTA HELENA DE GOIAS": ["GO"],
    "FORTALEZA": ["CE"],
    "SAO PAULO": ["SP"],
    "BOM JESUS": ["PI", "PE", "PB", "RS", "SC", "RN"],
    "PALMEIRA": ["PR", "SC"],
}


@pytest.fixture(autouse=True)
def ibge_offline(monkeypatch):
    monkeypatch.setattr(ibge, "_indice", dict(INDICE_FALSO))
    monkeypatch.setattr(ibge, "_carregar", lambda *a, **k: [])
    ibge._corrigir.cache_clear()
    ibge._inverter.cache_clear()
    yield


@pytest.fixture
def conexao_banco():
    from loterias.db import ConfiguracaoInvalida, conexao

    try:
        with conexao() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            yield conn
    except ConfiguracaoInvalida as e:
        pytest.skip(f"sem banco configurado: {e}")
    except Exception as e:
        pytest.skip(f"banco inacessivel: {e}")
