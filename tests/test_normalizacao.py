from __future__ import annotations

import json
from datetime import date

import pytest

from loterias.gold import normalizacao
from loterias.silver import ibge
from loterias.silver.normalizacao import nome_local_sorteio

INDICE_TESTE = {
    "SAO PAULO": ["SP"],
    "RIBEIRAO PRETO": ["SP"],
    "RIBEIRAO PIRES": ["SP"],
    "SANTA BARBARA D'OESTE": ["SP"],
    "SANTA CLARA D'OESTE": ["SP"],
    "EMBU DAS ARTES": ["SP"],
    "SAO MIGUEL DO OESTE": ["SC"],
    "BRASILIA": ["DF"],
    "FORTALEZA": ["CE"],
    "CIDADE ALFA": ["MG"],
    "CIDADE ALFE": ["MG"],
}


@pytest.fixture
def indice_teste(monkeypatch):
    monkeypatch.setattr(ibge, "_indice", dict(INDICE_TESTE))
    ibge._corrigir.cache_clear()
    ibge._inverter.cache_clear()


@pytest.fixture
def indice_real(monkeypatch):
    if not ibge.CACHE_FILE.exists():
        pytest.skip("cadastro do IBGE fora do cache local")
    dados = json.loads(ibge.CACHE_FILE.read_text(encoding="utf-8"))
    monkeypatch.setattr(ibge, "_indice", None)
    monkeypatch.setattr(ibge, "_carregar", lambda *a, **k: dados)
    ibge._corrigir.cache_clear()
    ibge._inverter.cache_clear()


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("Caminhão da Sorte", "CAMINHÃO DA SORTE"),
        ("CamInhão da Sorte", "CAMINHÃO DA SORTE"),
        ("Caminhão da Sorte09", "CAMINHÃO DA SORTE"),
        ("CAMINHÃO DA CAIXA", "CAMINHÃO DA SORTE"),
        ("ESPAÇO LOOTERIAS CAIXA", "ESPAÇO LOTERIAS CAIXA"),
        ("ÉSPAÇO LOTERIAS CAIXA", "ESPAÇO LOTERIAS CAIXA"),
        ("Espaço Caixa loterias", "ESPAÇO LOTERIAS CAIXA"),
        ("Estúdio de tv.", "ESTÚDIO DE TV"),
        ("ESTUDIO DE TV REDE GLOBO/SP", "ESTÚDIO DE TV REDE GLOBO"),
        ("PALCO PRINCIPAL - SÃO JOÃO", "PALCO PRINCIPAL - SÃO JOÃO"),
        ("QUINA", None),
        ("", None),
        ("Arena Nova", "ARENA NOVA"),
    ],
)
def test_local_do_sorteio_cai_no_nome_canonico(bruto, esperado):
    assert nome_local_sorteio(bruto) == esperado


@pytest.mark.parametrize(
    ("municipio", "uf", "esperado"),
    [
        ("SA0 PAULO", "SP", ("SAO PAULO", "SP")),
        ("SAO PULO", "SP", ("SAO PAULO", "SP")),
        ("São  Paulo", "SP", ("SAO PAULO", "SP")),
        ("SANTA BARBARA D OESTE", "SP", ("SANTA BARBARA D'OESTE", "SP")),
        ("SANTA BARBARA DOESTE", "SP", ("SANTA BARBARA D'OESTE", "SP")),
        ("SANTA BARBARA DO OESTE", "SP", ("SANTA BARBARA D'OESTE", "SP")),
        ("RIBEIRAO PRETO,", "SP", ("RIBEIRAO PRETO", "SP")),
        ("SAO MIGUEL DO OESTE", "SC", ("SAO MIGUEL DO OESTE", "SC")),
        ("BRASILIA", "SP", ("BRASILIA", "DF")),
        ("EMBU", "SP", ("EMBU DAS ARTES", "SP")),
        ("VARZEA GRANDE", "CE", ("VARZEA GRANDE", "CE")),
        ("CIDADE ALFO", "MG", ("CIDADE ALFO", "MG")),
        ("NAO INFORMADO", "SP", ("NAO INFORMADO", "SP")),
        ("CANAL ELETRONICO", "--", ("CANAL ELETRONICO", "--")),
    ],
)
def test_municipio_so_e_corrigido_com_evidencia(indice_teste, municipio, uf, esperado):
    assert ibge.corrigir_municipio(municipio, uf) == esperado


def _id(cur, sql: str, params: tuple) -> int:
    cur.execute(sql, params)
    return cur.fetchone()[0]


@pytest.mark.integracao
def test_reparo_unifica_localidade_local_e_loterica(conexao_banco, indice_real):
    with conexao_banco.cursor() as cur:
        cur.execute("SELECT id_tipo_jogo, id_faixa FROM public.faixa ORDER BY id_faixa LIMIT 1")
        linha = cur.fetchone()
        if linha is None:
            pytest.skip("faixa vazia")
        id_tipo_jogo, id_faixa = linha

        localidade = """
            INSERT INTO public.localidade (municipio, uf) VALUES (%s, %s)
            ON CONFLICT (municipio, uf) DO UPDATE SET municipio = EXCLUDED.municipio
            RETURNING id_localidade
        """
        certa = _id(cur, localidade, ("OSASCO", "SP"))
        errada = _id(cur, localidade, ("OSACO", "SP"))

        local = """
            INSERT INTO public.local_sorteio (nome, id_localidade) VALUES (%s, %s)
            ON CONFLICT (nome, id_localidade) DO UPDATE SET nome = EXCLUDED.nome
            RETURNING id_local_sorteio
        """
        local_certo = _id(cur, local, ("CAMINHÃO DA SORTE", certa))
        local_errado = _id(cur, local, ("CAMINHAO DA SORTE09", errada))

        concurso = _id(
            cur,
            """
            INSERT INTO public.concurso (id_tipo_jogo, numero_concurso, data_apuracao, id_local_sorteio)
            VALUES (%s, 900101, %s, %s) RETURNING id_concurso
            """,
            (id_tipo_jogo, date(2000, 1, 1), local_errado),
        )

        loterica = """
            INSERT INTO public.loterica (razao_social, canal_vendas, id_localidade)
            VALUES ('LOTERICA TESTE NORMALIZACAO', 'Fisico', %s) RETURNING id_loterica
        """
        loterica_certa = _id(cur, loterica, (certa,))
        loterica_errada = _id(cur, loterica, (errada,))
        for id_loterica, tipo in [
            (loterica_certa, "Simples"),
            (loterica_errada, "Simples"),
            (loterica_errada, "Bolao"),
        ]:
            cur.execute(
                """
                INSERT INTO public.ganhador_loterica (id_concurso, id_loterica, id_faixa, tipo_aposta)
                VALUES (%s, %s, %s, %s)
                """,
                (concurso, id_loterica, id_faixa, tipo),
            )
        cur.execute(
            "INSERT INTO public.ganhador_municipio (id_concurso, id_localidade, posicao) VALUES (%s, %s, 1)",
            (concurso, errada),
        )

        normalizacao.normalizar(cur)

        cur.execute("SELECT COUNT(*) FROM public.localidade WHERE id_localidade = %s", (errada,))
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT id_local_sorteio FROM public.concurso WHERE id_concurso = %s", (concurso,)
        )
        assert cur.fetchone()[0] == local_certo
        cur.execute(
            "SELECT COUNT(*) FROM public.loterica WHERE id_loterica = %s", (loterica_errada,)
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT tipo_aposta FROM public.ganhador_loterica WHERE id_concurso = %s AND id_loterica = %s ORDER BY 1",
            (concurso, loterica_certa),
        )
        assert [t for (t,) in cur.fetchall()] == ["Bolao", "Simples"]
        cur.execute(
            "SELECT id_localidade FROM public.ganhador_municipio WHERE id_concurso = %s",
            (concurso,),
        )
        assert [i for (i,) in cur.fetchall()] == [certa]
    conexao_banco.rollback()


@pytest.mark.integracao
def test_banco_nao_tem_nome_a_corrigir(conexao_banco, indice_real):
    with conexao_banco.cursor() as cur:
        pendentes = normalizacao.pendencias(cur)
    assert (pendentes.localidades, pendentes.locais_sorteio) == (0, 0)
