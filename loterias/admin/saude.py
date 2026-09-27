from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from loterias.admin import esquema
from loterias.db import conexao
from loterias.gold import data_proximo, normalizacao


@dataclass
class Cobertura:
    modalidade: str
    total_concursos: int
    ultimo_concurso: int | None
    ultima_apuracao: date | None
    atraso_dias: int | None
    situacao: str


@dataclass
class ContagemFila:
    fonte: str
    modalidade: str
    status: str
    quantidade: int


@dataclass
class Lacuna:
    modalidade: str
    concurso_faltando: int


@dataclass
class Consistencia:
    concursos_sem_anterior: int = 0
    data_proximo_invalida: int = 0
    lacunas: list[Lacuna] = field(default_factory=list)
    nomes: normalizacao.Pendencias = field(default_factory=normalizacao.Pendencias)

    @property
    def lacunas_por_modalidade(self) -> dict[str, int]:
        contagem: dict[str, int] = {}
        for lacuna in self.lacunas:
            contagem[lacuna.modalidade] = contagem.get(lacuna.modalidade, 0) + 1
        return contagem

    @property
    def ok(self) -> bool:
        return (
            not self.lacunas
            and self.concursos_sem_anterior == 0
            and self.data_proximo_invalida == 0
            and self.nomes.localidades == 0
            and self.nomes.locais_sorteio == 0
        )


@dataclass
class Diagnostico:
    cobertura: list[Cobertura] = field(default_factory=list)
    filas: list[ContagemFila] = field(default_factory=list)
    consistencia: Consistencia = field(default_factory=Consistencia)
    integridade: esquema.Integridade = field(default_factory=esquema.Integridade)
    arquivos_com_erro: int = 0
    abandonados: int = 0
    atrasadas: list[str] = field(default_factory=list)

    @property
    def degradado(self) -> bool:
        return bool(self.motivos)

    @property
    def motivos(self) -> list[str]:
        razoes = []
        if self.atrasadas:
            razoes.append(f"modalidades atrasadas: {', '.join(self.atrasadas)}")
        if self.abandonados:
            razoes.append(f"{self.abandonados} item(ns) abandonado(s) nas filas")
        if self.arquivos_com_erro:
            razoes.append(f"{self.arquivos_com_erro} arquivo(s) com erro em prata/ouro")
        if self.consistencia.lacunas:
            detalhe = ", ".join(
                f"{m}: {n}" for m, n in sorted(self.consistencia.lacunas_por_modalidade.items())
            )
            razoes.append(f"concursos faltando no meio da sequencia ({detalhe})")
        if self.consistencia.concursos_sem_anterior:
            razoes.append(
                f"{self.consistencia.concursos_sem_anterior} concurso(s) sem numero_concurso_anterior"
            )
        if self.consistencia.data_proximo_invalida:
            razoes.append(
                f"{self.consistencia.data_proximo_invalida} concurso(s) com data do proximo"
                " ausente ou impossivel"
            )
        if self.consistencia.nomes.localidades:
            razoes.append(
                f"{self.consistencia.nomes.localidades} localidade(s) com municipio a corrigir"
            )
        if self.consistencia.nomes.locais_sorteio:
            razoes.append(
                f"{self.consistencia.nomes.locais_sorteio} local(is) de sorteio fora do nome canonico"
            )
        if self.integridade.ausentes:
            razoes.append(f"tabelas ausentes: {', '.join(self.integridade.ausentes)}")
        for tabela in self.integridade.com_coluna_faltando:
            razoes.append(f"{tabela.nome} sem as colunas {', '.join(tabela.colunas_faltando)}")
        if self.integridade.sem_unicidade:
            razoes.append(
                f"sem constraint de unicidade: {', '.join(self.integridade.sem_unicidade)}"
            )
        for tabela in self.integridade.com_duplicata:
            razoes.append(f"{tabela.nome} com {tabela.duplicatas} grupo(s) de duplicatas")
        return razoes


def _consistencia(cur, modalidade: str | None) -> Consistencia:
    resultado = Consistencia()

    cur.execute(
        """
        SELECT COUNT(*) FROM public.concurso
        WHERE numero_concurso_anterior IS NULL AND numero_concurso > 1
        """
    )
    resultado.concursos_sem_anterior = cur.fetchone()[0]

    resultado.data_proximo_invalida = data_proximo.contar_reparaveis(cur)
    resultado.nomes = normalizacao.pendencias(cur)

    if modalidade:
        cur.execute(
            """
            SELECT modalidade, concurso_faltando FROM pipeline.v_saude_lacunas
            WHERE modalidade = %s ORDER BY modalidade, concurso_faltando
            """,
            (modalidade,),
        )
    else:
        cur.execute(
            """
            SELECT modalidade, concurso_faltando FROM pipeline.v_saude_lacunas
            ORDER BY modalidade, concurso_faltando
            """
        )
    resultado.lacunas = [Lacuna(*linha) for linha in cur.fetchall()]
    return resultado


def verificar(modalidade: str | None = None) -> Diagnostico:
    diagnostico = Diagnostico()
    filtro = "WHERE modalidade = %s" if modalidade else ""
    parametros = (modalidade,) if modalidade else ()

    with conexao() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT modalidade, total_concursos, ultimo_concurso, ultima_apuracao,
                   atraso_dias, situacao
            FROM pipeline.v_saude_cobertura
            {filtro}
            ORDER BY modalidade
            """,
            parametros,
        )
        diagnostico.cobertura = [Cobertura(*linha) for linha in cur.fetchall()]
        diagnostico.atrasadas = [
            c.modalidade for c in diagnostico.cobertura if c.situacao == "ATRASADO"
        ]

        cur.execute(
            f"""
            SELECT fonte, modalidade, status, qtd
            FROM pipeline.v_saude_filas
            {filtro}
            ORDER BY fonte, modalidade, status
            """,
            parametros,
        )
        diagnostico.filas = [ContagemFila(*linha) for linha in cur.fetchall()]
        diagnostico.abandonados = sum(
            f.quantidade for f in diagnostico.filas if f.status == "abandonado"
        )

        cur.execute(
            """
            SELECT COUNT(*) FROM pipeline.execucao_arquivo
            WHERE status_prata = 'erro' OR status_ouro = 'erro'
            """
        )
        diagnostico.arquivos_com_erro = cur.fetchone()[0]

        diagnostico.consistencia = _consistencia(cur, modalidade)

    diagnostico.integridade = esquema.verificar()
    return diagnostico
