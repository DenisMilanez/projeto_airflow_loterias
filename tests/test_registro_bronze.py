from __future__ import annotations

import pytest

from loterias.admin.registro_bronze import (
    FILA_POR_FONTE,
    PADRAO_PARQUET,
    registrar_bronze,
)


@pytest.mark.parametrize(
    ("nome", "fonte", "ini", "fim", "parcial"),
    [
        ("megasena_concursos_0001_0500_20260527.parquet", "concursos", 1, 500, None),
        ("quina_concursos_7047_7048_20260612.parquet", "concursos", 7047, 7048, None),
        ("lotofacil_locais_sorte_2028_2037_20260530.parquet", "locais_sorte", 2028, 2037, None),
        (
            "megasena_locais_sorte_2550_2550_PARCIAL_20260608.parquet",
            "locais_sorte",
            2550,
            2550,
            "PARCIAL",
        ),
    ],
)
def test_padrao_reconhece_nomes_reais(nome, fonte, ini, fim, parcial):
    achado = PADRAO_PARQUET.match(nome)
    assert achado is not None, f"nome nao reconhecido: {nome}"
    assert achado.group("fonte") == fonte
    assert int(achado.group("ini")) == ini
    assert int(achado.group("fim")) == fim
    assert achado.group("parcial") == parcial


def test_arquivo_parcial_nao_e_descartado_em_silencio():
    nome = "megasena_locais_sorte_2810_2810_PARCIAL_20260608.parquet"
    assert PADRAO_PARQUET.match(nome) is not None


@pytest.mark.parametrize(
    "nome",
    [
        "megasena_concursos_0001_0500.parquet",
        "megasena_outra_fonte_0001_0500_20260527.parquet",
        "relatorio.parquet",
    ],
)
def test_padrao_rejeita_nomes_fora_do_formato(nome):
    assert PADRAO_PARQUET.match(nome) is None


def test_cada_fonte_tem_sua_fila():
    assert set(FILA_POR_FONTE) == {"concursos", "locais_sorte"}


def test_locais_sorte_tambem_enfileira_concursos():
    resultado = registrar_bronze(modalidades=["LOTOMANIA"], fontes=("locais_sorte",), dry_run=True)
    assert resultado.arquivos > 0
    assert resultado.concursos_enfileirados > 0, (
        "sem enfileirar, a fila de locais da sorte nasce vazia e o scraper "
        "rebaixa da API concursos que ja estao no bronze"
    )
