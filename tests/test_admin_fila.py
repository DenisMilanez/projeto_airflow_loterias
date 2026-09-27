from __future__ import annotations

import pytest

from loterias.admin import arquivo, banco, fila


def test_reset_sem_modalidade_falha_em_vez_de_agir_calado():
    with pytest.raises(fila.ModalidadeNaoInformada):
        fila.resetar(de="erro", fonte="concursos")


def test_modalidade_e_todas_juntas_e_erro():
    with pytest.raises(fila.ModalidadeNaoInformada):
        fila.resetar(de="erro", fonte="concursos", modalidade="MEGA_SENA", todas=True)


def test_fonte_invalida_e_recusada():
    with pytest.raises(fila.FonteInvalida):
        fila.resetar(de="erro", fonte="inexistente", todas=True)


def test_status_nao_resetavel_e_recusado():
    with pytest.raises(fila.StatusInvalido):
        fila.resetar(de="concluido", fonte="concursos", todas=True)


def test_camada_invalida_e_recusada():
    with pytest.raises(arquivo.CamadaInvalida):
        arquivo.resetar(camada="bronze")


def test_intervalo_de_id_pela_metade_e_recusado():
    with pytest.raises(ValueError):
        arquivo.resetar(camada="prata", id_inicio=1)


@pytest.mark.parametrize("operacao", [banco.limpar, banco.recriar])
def test_destrutivo_exige_confirmacao(operacao):
    with pytest.raises(banco.ConfirmacaoAusente):
        operacao()


def test_todas_expande_para_as_modalidades_do_yaml():
    from loterias.config import listar_modalidades

    assert fila._alvos(None, todas=True) == listar_modalidades()
