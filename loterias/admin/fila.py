from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from loterias.config import listar_modalidades
from loterias.db import conexao

TABELA_POR_FONTE = {
    "concursos": "pipeline.concurso_fila",
    "locais_sorte": "pipeline.locais_sorte_fila",
}

STATUS_RESETAVEIS = ("erro", "processando", "abandonado")


class FonteInvalida(ValueError):
    pass


class ModalidadeNaoInformada(ValueError):
    pass


class StatusInvalido(ValueError):
    pass


@dataclass
class LinhaFila:
    modalidade: str
    status: str
    quantidade: int
    ultima_tentativa: datetime | None


@dataclass
class ErroAgrupado:
    modalidade: str
    detalhe: str
    quantidade: int


@dataclass
class ItemFila:
    modalidade: str
    numero_concurso: int
    status: str
    tentativas: int
    max_tentativas: int
    ultima_tentativa: datetime | None
    proximo_retry: datetime | None
    detalhe: str


@dataclass
class ResultadoReset:
    afetados: int
    dry_run: bool
    modalidades: list[str]
    fonte: str
    de: list[str]
    antes: dict[str, int] = field(default_factory=dict)
    depois: dict[str, int] = field(default_factory=dict)


def _tabela(fonte: str) -> str:
    if fonte not in TABELA_POR_FONTE:
        raise FonteInvalida(f"fonte '{fonte}' invalida; use {' ou '.join(TABELA_POR_FONTE)}")
    return TABELA_POR_FONTE[fonte]


def _alvos(modalidade: str | None, todas: bool) -> list[str]:
    if todas and modalidade:
        raise ModalidadeNaoInformada("use --modalidade ou --todas, nunca os dois")
    if todas:
        return listar_modalidades()
    if not modalidade:
        raise ModalidadeNaoInformada(
            "informe --modalidade MEGA_SENA ou --todas; nao existe default silencioso"
        )
    return [modalidade]


def _contagem(cur, tabela: str, modalidades: list[str]) -> dict[str, int]:
    cur.execute(
        f"SELECT status, COUNT(*) FROM {tabela} WHERE modalidade = ANY(%s) GROUP BY status",
        (modalidades,),
    )
    return dict(cur.fetchall())


def status(
    fonte: str = "locais_sorte",
    modalidade: str | None = None,
    todas: bool = True,
) -> list[LinhaFila]:
    tabela = _tabela(fonte)
    alvos = _alvos(modalidade, todas and modalidade is None)
    with conexao() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT modalidade, status, COUNT(*), MAX(ultima_tentativa_em)
            FROM {tabela}
            WHERE modalidade = ANY(%s)
            GROUP BY modalidade, status
            ORDER BY modalidade, status
            """,
            (alvos,),
        )
        return [LinhaFila(m, s, q, u) for m, s, q, u in cur.fetchall()]


def resetar(
    de: str | list[str],
    fonte: str = "locais_sorte",
    modalidade: str | None = None,
    todas: bool = False,
    dias: int | None = None,
    zerar_tentativas: bool = True,
    dry_run: bool = False,
) -> ResultadoReset:
    tabela = _tabela(fonte)
    alvos = _alvos(modalidade, todas)
    origens = [de] if isinstance(de, str) else list(de)
    invalidos = [s for s in origens if s not in STATUS_RESETAVEIS]
    if invalidos:
        raise StatusInvalido(f"status {invalidos} nao reseta; use {' ou '.join(STATUS_RESETAVEIS)}")

    filtro = "WHERE modalidade = ANY(%s) AND status = ANY(%s)"
    parametros: list = [alvos, origens]
    if dias is not None:
        filtro += " AND ultima_tentativa_em < NOW() - (%s || ' days')::INTERVAL"
        parametros.append(str(dias))

    tentativas = "0" if zerar_tentativas else "GREATEST(tentativas - 1, 0)"

    with conexao() as conn, conn.cursor() as cur:
        antes = _contagem(cur, tabela, alvos)
        cur.execute(f"SELECT COUNT(*) FROM {tabela} {filtro}", parametros)
        candidatos = cur.fetchone()[0]

        if dry_run:
            return ResultadoReset(candidatos, True, alvos, fonte, origens, antes, antes)

        cur.execute(
            f"""
            UPDATE {tabela}
            SET status = 'pendente',
                tentativas = {tentativas},
                proximo_retry_em = NULL,
                erro_detalhe = NULL,
                atualizado_em = NOW()
            {filtro}
            """,
            parametros,
        )
        afetados = cur.rowcount
        depois = _contagem(cur, tabela, alvos)
        conn.commit()

    return ResultadoReset(afetados, False, alvos, fonte, origens, antes, depois)


def erros(
    fonte: str = "locais_sorte",
    modalidade: str | None = None,
    todas: bool = True,
    limite: int = 10,
) -> list[ErroAgrupado]:
    tabela = _tabela(fonte)
    alvos = _alvos(modalidade, todas and modalidade is None)
    with conexao() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT modalidade, COALESCE(NULLIF(TRIM(erro_detalhe), ''), '(sem detalhe)'),
                   COUNT(*) AS qtd
            FROM {tabela}
            WHERE modalidade = ANY(%s) AND status IN ('erro', 'abandonado')
            GROUP BY 1, 2
            ORDER BY qtd DESC, 1, 2
            LIMIT %s
            """,
            (alvos, limite),
        )
        return [ErroAgrupado(*linha) for linha in cur.fetchall()]


def detalhe(
    fonte: str = "locais_sorte",
    modalidade: str | None = None,
    todas: bool = True,
    status: tuple[str, ...] = ("processando", "erro", "abandonado"),
    limite: int = 50,
) -> list[ItemFila]:
    tabela = _tabela(fonte)
    alvos = _alvos(modalidade, todas and modalidade is None)
    with conexao() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT modalidade, numero_concurso, status, tentativas, max_tentativas,
                   ultima_tentativa_em, proximo_retry_em,
                   LEFT(COALESCE(erro_detalhe, ''), 120)
            FROM {tabela}
            WHERE modalidade = ANY(%s) AND status = ANY(%s)
            ORDER BY modalidade, numero_concurso
            LIMIT %s
            """,
            (alvos, list(status), limite),
        )
        return [ItemFila(*linha) for linha in cur.fetchall()]


def faixa(fonte: str = "locais_sorte", modalidade: str | None = None, todas: bool = True) -> dict:
    tabela = _tabela(fonte)
    alvos = _alvos(modalidade, todas and modalidade is None)
    with conexao() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT MIN(numero_concurso), MAX(numero_concurso), COUNT(*)
            FROM {tabela} WHERE modalidade = ANY(%s)
            """,
            (alvos,),
        )
        menor, maior, total = cur.fetchone()
    return {"menor": menor, "maior": maior, "total": total}
