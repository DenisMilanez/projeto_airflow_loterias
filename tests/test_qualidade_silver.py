from __future__ import annotations

import pandas as pd
import pytest

from loterias.silver.validacao import (
    SCHEMAS,
    ValidacaoFalhou,
    validar_dataframe,
    validar_pasta_silver,
)


def _localidade(**alteracoes) -> pd.DataFrame:
    base = {"municipio": ["CURITIBA"], "uf": ["PR"]}
    base.update(alteracoes)
    return pd.DataFrame(base)


def test_localidade_valida_passa():
    assert validar_dataframe("localidade", _localidade()) == []


def test_uf_de_um_caractere_e_reprovada():
    falhas = validar_dataframe("localidade", _localidade(uf=["G"]))
    assert falhas, "UF truncada deveria reprovar na validacao"
    assert any(f.coluna == "uf" for f in falhas)


def test_canal_eletronico_e_uf_conhecida_e_aceito():
    assert (
        validar_dataframe("localidade", _localidade(municipio=["CANAL ELETRONICO"], uf=["--"]))
        == []
    )
    assert (
        validar_dataframe("localidade", _localidade(municipio=["NAO INFORMADO"], uf=["NA"])) == []
    )


def test_municipio_vazio_e_reprovado():
    falhas = validar_dataframe("localidade", _localidade(municipio=[""]))
    assert any(f.coluna == "municipio" for f in falhas)


def test_localidade_duplicada_e_reprovada():
    df = pd.DataFrame({"municipio": ["CURITIBA", "CURITIBA"], "uf": ["PR", "PR"]})
    assert validar_dataframe("localidade", df)


def test_concurso_com_numero_zero_e_reprovado():
    df = pd.DataFrame(
        {
            "numero_concurso": [0],
            "codigo_tipo_jogo": ["MEGA_SENA"],
            "localidade_uf": ["SP"],
            "localidade_municipio": ["SAO PAULO"],
            "acumulado": [False],
            "valor_arrecadado": [10.0],
        }
    )
    assert any(f.coluna == "numero_concurso" for f in validar_dataframe("concurso", df))


def test_valor_arrecadado_negativo_e_reprovado():
    df = pd.DataFrame(
        {
            "numero_concurso": [1],
            "codigo_tipo_jogo": ["MEGA_SENA"],
            "localidade_uf": ["SP"],
            "localidade_municipio": ["SAO PAULO"],
            "acumulado": [False],
            "valor_arrecadado": [-1.0],
        }
    )
    assert any(f.coluna == "valor_arrecadado" for f in validar_dataframe("concurso", df))


def test_dezena_fora_do_intervalo_e_reprovada():
    df = pd.DataFrame(
        {
            "numero_concurso": [1],
            "codigo_tipo_jogo": ["MEGA_SENA"],
            "numero": [101],
            "ordem_sorteio": [1],
            "segundo_sorteio": [False],
        }
    )
    assert any(f.coluna == "numero" for f in validar_dataframe("dezena", df))


def test_tabela_sem_schema_nao_reprova():
    assert validar_dataframe("tabela_desconhecida", pd.DataFrame({"x": [1]})) == []


def test_modo_estrito_interrompe_a_carga(tmp_path):
    _localidade(uf=["G"]).to_parquet(tmp_path / "localidade.parquet", index=False)
    with pytest.raises(ValidacaoFalhou):
        validar_pasta_silver(tmp_path, estrito=True)


def test_modo_permissivo_apenas_relata(tmp_path):
    _localidade(uf=["G"]).to_parquet(tmp_path / "localidade.parquet", index=False)
    relatorio = validar_pasta_silver(tmp_path, estrito=False)
    assert not relatorio.ok
    assert relatorio.falhas


def test_todas_as_tabelas_do_silver_tem_schema():
    esperadas = {
        "concurso",
        "dezena",
        "rateio",
        "faixa",
        "localidade",
        "local_sorteio",
        "ganhador_municipio",
        "loterica",
        "ganhador_loterica",
    }
    assert esperadas <= set(SCHEMAS)
