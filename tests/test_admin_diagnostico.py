from __future__ import annotations

import pytest

from loterias.admin import esquema, fila

pytestmark = pytest.mark.integracao


def test_todas_as_tabelas_de_negocio_tem_expectativa_declarada():
    esperadas = {
        "tipo_jogo",
        "faixa",
        "localidade",
        "local_sorteio",
        "concurso",
        "dezena",
        "rateio",
        "ganhador_municipio",
        "loterica",
        "ganhador_loterica",
    }
    assert esperadas == set(esquema.COLUNAS_ESPERADAS)


def test_schema_do_banco_bate_com_o_esperado(conexao_banco):
    integridade = esquema.verificar()
    assert not integridade.ausentes, f"tabelas ausentes: {integridade.ausentes}"
    faltando = {t.nome: t.colunas_faltando for t in integridade.com_coluna_faltando}
    assert not faltando, f"colunas faltando: {faltando}"


def test_toda_tabela_de_negocio_tem_constraint_de_unicidade(conexao_banco):
    integridade = esquema.verificar()
    assert not integridade.sem_unicidade, (
        f"sem unicidade, risco de duplicata: {integridade.sem_unicidade}"
    )


def test_nao_existem_duplicatas_nas_chaves_naturais(conexao_banco):
    integridade = esquema.verificar()
    duplicadas = {t.nome: t.duplicatas for t in integridade.com_duplicata}
    assert not duplicadas, f"duplicatas encontradas: {duplicadas}"


def test_constraint_de_local_sorteio_existe(conexao_banco):
    integridade = esquema.verificar()
    local = next(t for t in integridade.tabelas if t.nome == "local_sorteio")
    assert any("nome" in c and "id_localidade" in c for c in local.unicidade)


def test_faixa_nao_e_identificada_so_pelos_acertos(conexao_banco):
    integridade = esquema.verificar()
    faixa_tabela = next(t for t in integridade.tabelas if t.nome == "faixa")
    assert any("numero_faixa" in u for u in faixa_tabela.unicidade)
    assert not any(u.endswith("(id_tipo_jogo, acertos)") for u in faixa_tabela.unicidade)


@pytest.mark.parametrize("funcao", [fila.erros, fila.detalhe, fila.faixa])
def test_diagnostico_de_fila_exige_modalidade_ou_todas(funcao):
    with pytest.raises(fila.ModalidadeNaoInformada):
        funcao(fonte="concursos", todas=False)


@pytest.mark.parametrize("funcao", [fila.erros, fila.detalhe, fila.faixa])
def test_diagnostico_de_fila_recusa_fonte_invalida(funcao):
    with pytest.raises(fila.FonteInvalida):
        funcao(fonte="inexistente")


def test_reset_aceita_varios_status_de_uma_vez(conexao_banco):
    resultado = fila.resetar(
        de=["erro", "processando", "abandonado"],
        fonte="locais_sorte",
        todas=True,
        dry_run=True,
    )
    assert resultado.de == ["erro", "processando", "abandonado"]
    assert resultado.dry_run


def test_faixa_devolve_os_limites_da_fila(conexao_banco):
    extensao = fila.faixa(fonte="concursos", todas=True)
    assert set(extensao) == {"menor", "maior", "total"}
    if extensao["total"]:
        assert extensao["menor"] <= extensao["maior"]
