from __future__ import annotations

from dataclasses import dataclass, field

from loterias.db import conexao

COLUNAS_ESPERADAS: dict[str, set[str]] = {
    "tipo_jogo": {
        "id_tipo_jogo",
        "codigo",
        "nome_exibicao",
        "dezenas_sorteadas",
        "dezenas_apostadas",
        "dezenas_disponiveis",
    },
    "localidade": {"municipio", "uf"},
    "faixa": {"id_tipo_jogo", "numero_faixa", "descricao", "acertos"},
    "local_sorteio": {"nome", "id_localidade"},
    "concurso": {
        "id_tipo_jogo",
        "numero_concurso",
        "data_apuracao",
        "data_proximo_concurso",
        "numero_concurso_anterior",
        "numero_concurso_proximo",
        "numero_concurso_final_0_5",
        "id_local_sorteio",
        "acumulado",
        "ultimo_concurso",
        "indicador_concurso_especial",
        "tipo_publicacao",
        "numero_jogo",
        "observacao",
        "valor_arrecadado",
        "valor_estimado_proximo_concurso",
        "valor_acumulado_proximo_concurso",
        "valor_acumulado_concurso_especial",
        "valor_acumulado_concurso_0_5",
        "valor_saldo_reserva_garantidora",
        "valor_total_premio_faixa_um",
    },
    "dezena": {
        "id_concurso",
        "numero",
        "ordem_sorteio",
        "ordem_crescente",
        "segundo_sorteio",
    },
    "rateio": {
        "id_concurso",
        "id_faixa",
        "numero_ganhadores",
        "valor_premio",
        "valor_total",
    },
    "ganhador_municipio": {
        "id_concurso",
        "id_localidade",
        "numero_ganhadores",
        "posicao",
        "nome_fantasia_ul",
        "serie",
    },
    "loterica": {"razao_social", "nome_fantasia", "id_localidade", "canal_vendas"},
    "ganhador_loterica": {
        "id_concurso",
        "id_loterica",
        "id_faixa",
        "canal_vendas",
        "tipo_aposta",
        "numero_cotas",
        "quantidade_numeros_apostados",
        "quantidade_premios_por_faixa",
        "premio_total",
        "teimosinha",
    },
}

CHAVES_DE_DUPLICATA: dict[str, tuple[str, ...]] = {
    "ganhador_municipio": ("id_concurso", "id_localidade", "posicao"),
    "ganhador_loterica": ("id_concurso", "id_loterica", "id_faixa", "tipo_aposta"),
    "localidade": ("municipio", "uf"),
    "local_sorteio": ("nome", "id_localidade"),
}


@dataclass
class Tabela:
    nome: str
    existe: bool
    colunas_faltando: list[str] = field(default_factory=list)
    unicidade: list[str] = field(default_factory=list)
    duplicatas: int | None = None

    @property
    def ok(self) -> bool:
        return (
            self.existe
            and not self.colunas_faltando
            and bool(self.unicidade)
            and not self.duplicatas
        )


@dataclass
class Integridade:
    tabelas: list[Tabela] = field(default_factory=list)

    @property
    def ausentes(self) -> list[str]:
        return [t.nome for t in self.tabelas if not t.existe]

    @property
    def com_coluna_faltando(self) -> list[Tabela]:
        return [t for t in self.tabelas if t.existe and t.colunas_faltando]

    @property
    def sem_unicidade(self) -> list[str]:
        return [t.nome for t in self.tabelas if t.existe and not t.unicidade]

    @property
    def com_duplicata(self) -> list[Tabela]:
        return [t for t in self.tabelas if t.duplicatas]

    @property
    def ok(self) -> bool:
        return all(t.ok for t in self.tabelas)


def _colunas(cur, tabela: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (tabela,),
    )
    return {linha[0] for linha in cur.fetchall()}


def _unicidade(cur, tabela: str) -> list[str]:
    cur.execute(
        """
        SELECT c.conname, array_agg(a.attname ORDER BY a.attnum)
        FROM pg_constraint c
        JOIN pg_class r      ON r.oid = c.conrelid
        JOIN pg_namespace n  ON n.oid = r.relnamespace
        JOIN pg_attribute a  ON a.attrelid = r.oid AND a.attnum = ANY(c.conkey)
        WHERE n.nspname = 'public' AND r.relname = %s AND c.contype IN ('u', 'p')
        GROUP BY c.conname
        ORDER BY c.conname
        """,
        (tabela,),
    )
    return [f"{nome} ({', '.join(colunas)})" for nome, colunas in cur.fetchall()]


def _duplicatas(cur, tabela: str, chave: tuple[str, ...]) -> int:
    colunas = ", ".join(chave)
    cur.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT {colunas} FROM public.{tabela}
            GROUP BY {colunas} HAVING COUNT(*) > 1
        ) repetidos
        """
    )
    return cur.fetchone()[0]


def verificar() -> Integridade:
    integridade = Integridade()
    with conexao() as conn, conn.cursor() as cur:
        for nome, esperadas in COLUNAS_ESPERADAS.items():
            presentes = _colunas(cur, nome)
            if not presentes:
                integridade.tabelas.append(Tabela(nome, existe=False))
                continue

            tabela = Tabela(
                nome=nome,
                existe=True,
                colunas_faltando=sorted(esperadas - presentes),
                unicidade=_unicidade(cur, nome),
            )
            chave = CHAVES_DE_DUPLICATA.get(nome)
            if chave and not tabela.colunas_faltando and set(chave) <= presentes:
                tabela.duplicatas = _duplicatas(cur, nome, chave)
            integridade.tabelas.append(tabela)

    return integridade
