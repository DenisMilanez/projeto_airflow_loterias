from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from psycopg2.extras import execute_values

from loterias import caminhos
from loterias.config import codigo_api, listar_modalidades
from loterias.db import conexao

PADRAO_PARQUET = re.compile(
    r"^(?P<jogo>\w+?)_(?P<fonte>concursos|locais_sorte)_(?P<ini>\d+)_(?P<fim>\d+)"
    r"(?:_(?P<parcial>PARCIAL))?_(?P<data>\d{8})\.parquet$"
)

TIPO_POR_FONTE = {"concursos": "historico", "locais_sorte": "locais_sorte"}

FILA_POR_FONTE = {
    "concursos": "pipeline.concurso_fila",
    "locais_sorte": "pipeline.locais_sorte_fila",
}


@dataclass
class ResultadoRegistro:
    arquivos: int = 0
    linhas: int = 0
    concursos_enfileirados: int = 0
    ignorados: list[str] = field(default_factory=list)
    por_modalidade: dict[str, int] = field(default_factory=dict)


def pasta_de(modalidade: str, fonte: str) -> Path:
    return caminhos.bronze() / codigo_api(modalidade) / fonte


def _contar_linhas(parquet: Path, coluna: str, ini: int, fim: int) -> int:
    try:
        return len(pd.read_parquet(parquet, columns=[coluna]))
    except Exception:
        return fim - ini + 1


def registrar_bronze(
    modalidades: list[str] | None = None,
    fontes: tuple[str, ...] = ("concursos", "locais_sorte"),
    dry_run: bool = False,
) -> ResultadoRegistro:
    alvos = modalidades or listar_modalidades()
    resultado = ResultadoRegistro()
    agora = datetime.now(UTC)

    for modalidade in alvos:
        for fonte in fontes:
            pasta = pasta_de(modalidade, fonte)
            if not pasta.exists():
                continue
            parquets = sorted(pasta.rglob("*.parquet"), key=lambda p: p.name)
            if not parquets:
                continue

            registros = []
            concursos = set()
            for parquet in parquets:
                achado = PADRAO_PARQUET.match(parquet.name)
                if not achado:
                    resultado.ignorados.append(parquet.name)
                    continue
                ini, fim = int(achado.group("ini")), int(achado.group("fim"))
                total = _contar_linhas(parquet, "numero_concurso", ini, fim)
                registros.append((str(parquet.resolve()), ini, fim, total))
                resultado.linhas += total
                concursos.update(range(ini, fim + 1))

            if not registros:
                continue

            resultado.arquivos += len(registros)
            resultado.por_modalidade[f"{modalidade}/{fonte}"] = len(registros)
            resultado.concursos_enfileirados += len(concursos)

            if dry_run:
                continue

            with conexao() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO pipeline.execucao (
                            modalidade, tipo, status, iniciado_em, finalizado_em,
                            total_coletados, fonte
                        ) VALUES (%s, %s, 'sucesso', %s, %s, %s, %s)
                        RETURNING id_execucao
                        """,
                        (modalidade, TIPO_POR_FONTE[fonte], agora, agora, len(registros), fonte),
                    )
                    id_execucao = cur.fetchone()[0]

                    execute_values(
                        cur,
                        """
                        INSERT INTO pipeline.execucao_arquivo (
                            id_execucao, caminho_arquivo, concurso_inicio, concurso_fim,
                            total_registros, fonte, status_prata, status_ouro
                        ) VALUES %s
                        """,
                        [
                            (id_execucao, c, i, f, t, fonte, "pendente", None)
                            for c, i, f, t in registros
                        ],
                        page_size=200,
                    )

                    if concursos:
                        execute_values(
                            cur,
                            f"""
                            INSERT INTO {FILA_POR_FONTE[fonte]}
                                (modalidade, numero_concurso, status, tentativas)
                            VALUES %s
                            ON CONFLICT (modalidade, numero_concurso) DO NOTHING
                            """,
                            [(modalidade, n, "concluido", 1) for n in sorted(concursos)],
                            page_size=1000,
                        )
                conn.commit()

    return resultado
