from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema

from loterias.log import aviso, evento

UF_VALIDA = Check.str_matches(r"^([A-Z]{2}|--|NA)$")
MUNICIPIO_PREENCHIDO = Check.str_length(min_value=1)

SCHEMA_CONCURSO = DataFrameSchema(
    {
        "numero_concurso": Column(int, Check.gt(0)),
        "codigo_tipo_jogo": Column(str, MUNICIPIO_PREENCHIDO),
        "localidade_uf": Column(str, UF_VALIDA, nullable=True),
        "localidade_municipio": Column(str, nullable=True),
        "acumulado": Column(bool),
        "valor_arrecadado": Column(float, Check.gt(0), nullable=True),
    },
    strict=False,
    unique=["numero_concurso", "codigo_tipo_jogo"],
)

SCHEMA_DEZENA = DataFrameSchema(
    {
        "numero_concurso": Column(int, Check.gt(0)),
        "codigo_tipo_jogo": Column(str),
        "numero": Column(int, Check.in_range(0, 100)),
        "ordem_sorteio": Column(int, Check.ge(0)),
        "segundo_sorteio": Column(bool),
    },
    strict=False,
)

SCHEMA_RATEIO = DataFrameSchema(
    {
        "numero_concurso": Column(int, Check.gt(0)),
        "numero_faixa": Column(int, Check.gt(0)),
        "numero_ganhadores": Column(int, Check.ge(0)),
        "valor_premio": Column(float, Check.ge(0), nullable=True),
    },
    strict=False,
)

SCHEMA_FAIXA = DataFrameSchema(
    {
        "codigo_tipo_jogo": Column(str),
        "numero_faixa": Column(int, Check.gt(0)),
        "acertos": Column(int, Check.ge(0), nullable=True),
    },
    strict=False,
    unique=["codigo_tipo_jogo", "numero_faixa"],
)

SCHEMA_LOCALIDADE = DataFrameSchema(
    {
        "municipio": Column(str, MUNICIPIO_PREENCHIDO),
        "uf": Column(str, UF_VALIDA),
    },
    strict=False,
    unique=["municipio", "uf"],
)

SCHEMA_LOCAL_SORTEIO = DataFrameSchema(
    {
        "nome": Column(str, MUNICIPIO_PREENCHIDO),
        "municipio": Column(str),
        "uf": Column(str, UF_VALIDA),
    },
    strict=False,
)

SCHEMA_GANHADOR_MUNICIPIO = DataFrameSchema(
    {
        "numero_concurso": Column(int, Check.gt(0)),
        "municipio": Column(str),
        "uf": Column(str, UF_VALIDA),
        "numero_ganhadores": Column(int, Check.ge(0)),
    },
    strict=False,
)

SCHEMA_LOTERICA = DataFrameSchema(
    {
        "nome_fantasia": Column(str, nullable=True),
        "municipio": Column(str, nullable=True),
        "uf": Column(str, UF_VALIDA, nullable=True),
    },
    strict=False,
)

SCHEMA_GANHADOR_LOTERICA = DataFrameSchema(
    {
        "numero_concurso": Column(int, Check.gt(0)),
        "numero_ganhadores": Column(int, Check.ge(0), nullable=True),
    },
    strict=False,
)

SCHEMAS: dict[str, DataFrameSchema] = {
    "concurso": SCHEMA_CONCURSO,
    "dezena": SCHEMA_DEZENA,
    "rateio": SCHEMA_RATEIO,
    "faixa": SCHEMA_FAIXA,
    "localidade": SCHEMA_LOCALIDADE,
    "local_sorteio": SCHEMA_LOCAL_SORTEIO,
    "ganhador_municipio": SCHEMA_GANHADOR_MUNICIPIO,
    "loterica": SCHEMA_LOTERICA,
    "ganhador_loterica": SCHEMA_GANHADOR_LOTERICA,
}


class ValidacaoFalhou(RuntimeError):
    pass


@dataclass
class Falha:
    tabela: str
    coluna: str | None
    checagem: str
    exemplos: list


@dataclass
class Relatorio:
    pasta: Path
    tabelas_validadas: list[str] = field(default_factory=list)
    tabelas_sem_schema: list[str] = field(default_factory=list)
    falhas: list[Falha] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.falhas


def _falhas_de(tabela: str, erro: pa.errors.SchemaErrors) -> list[Falha]:
    casos = erro.failure_cases
    agrupado = casos.groupby(["column", "check"], dropna=False)["failure_case"]
    return [
        Falha(tabela, coluna, str(checagem), list(valores.head(3)))
        for (coluna, checagem), valores in agrupado
    ]


def validar_dataframe(tabela: str, df: pd.DataFrame) -> list[Falha]:
    schema = SCHEMAS.get(tabela)
    if schema is None:
        return []
    try:
        schema.validate(df, lazy=True)
    except pa.errors.SchemaErrors as erro:
        return _falhas_de(tabela, erro)
    return []


def validar_pasta_silver(pasta: Path, estrito: bool = False) -> Relatorio:
    relatorio = Relatorio(pasta=pasta)
    for parquet in sorted(pasta.glob("*.parquet")):
        tabela = parquet.stem
        if tabela not in SCHEMAS:
            relatorio.tabelas_sem_schema.append(tabela)
            continue
        falhas = validar_dataframe(tabela, pd.read_parquet(parquet))
        relatorio.tabelas_validadas.append(tabela)
        relatorio.falhas.extend(falhas)

    for falha in relatorio.falhas:
        aviso(
            f"qualidade: {falha.tabela}.{falha.coluna} falhou em '{falha.checagem}' "
            f"exemplos={falha.exemplos}"
        )

    evento(
        "validacao_silver",
        pasta=pasta.name,
        tabelas=len(relatorio.tabelas_validadas),
        falhas=len(relatorio.falhas),
    )

    if estrito and not relatorio.ok:
        raise ValidacaoFalhou(
            f"{len(relatorio.falhas)} falha(s) de qualidade em {pasta}; gold nao sera carregado"
        )
    return relatorio
