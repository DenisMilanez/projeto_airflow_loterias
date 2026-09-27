from __future__ import annotations

from dataclasses import dataclass

from loterias.db import conexao

CAMADAS = ("prata", "ouro")


class CamadaInvalida(ValueError):
    pass


@dataclass
class ResultadoResetArquivo:
    afetados: int
    dry_run: bool
    camada: str


def resetar(
    camada: str,
    id_inicio: int | None = None,
    id_fim: int | None = None,
    dry_run: bool = False,
) -> ResultadoResetArquivo:
    if camada not in CAMADAS:
        raise CamadaInvalida(f"camada '{camada}' invalida; use {' ou '.join(CAMADAS)}")

    if camada == "prata":
        alvo = "status_prata = 'pendente', status_ouro = NULL"
        condicao = "status_prata IS DISTINCT FROM 'pendente'"
    else:
        alvo = "status_ouro = NULL"
        condicao = "status_ouro IS NOT NULL"

    parametros: list = []
    if id_inicio is not None and id_fim is not None:
        condicao += " AND id_arquivo BETWEEN %s AND %s"
        parametros += [id_inicio, id_fim]
    elif id_inicio is not None or id_fim is not None:
        raise ValueError("informe --id-inicio e --id-fim juntos, ou nenhum dos dois")

    with conexao() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM pipeline.execucao_arquivo WHERE {condicao}", parametros)
        candidatos = cur.fetchone()[0]
        if dry_run:
            return ResultadoResetArquivo(candidatos, True, camada)

        cur.execute(f"UPDATE pipeline.execucao_arquivo SET {alvo} WHERE {condicao}", parametros)
        afetados = cur.rowcount
        conn.commit()

    return ResultadoResetArquivo(afetados, False, camada)
