from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field

from loterias import caminhos
from loterias.db import conexao, descrever_conexao

TABELAS_NEGOCIO = (
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
)

MODULOS_SCHEMA = ("loterias.schema.publico", "loterias.schema.observabilidade")


class ConfirmacaoAusente(RuntimeError):
    pass


@dataclass
class Conexao:
    descricao: str
    banco: str
    versao: str


@dataclass
class Tabela:
    schema: str
    nome: str
    linhas: int


@dataclass
class TipoJogoSeed:
    id_tipo_jogo: int
    codigo: str
    nome_exibicao: str
    dezenas_sorteadas: int
    dezenas_apostadas: int
    dezenas_disponiveis: int


@dataclass
class ResultadoRecriar:
    modulos_ok: list[str] = field(default_factory=list)
    modulos_falhos: list[str] = field(default_factory=list)
    arquivos_bronze: int = 0
    concursos_enfileirados: int = 0
    seed: list[TipoJogoSeed] = field(default_factory=list)


def _exigir_confirmacao(confirmar: bool, operacao: str) -> None:
    if not confirmar:
        raise ConfirmacaoAusente(f"'{operacao}' e destrutivo e exige --confirmar explicito")


def testar() -> Conexao:
    with conexao() as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database(), version()")
        banco, versao = cur.fetchone()
    return Conexao(descrever_conexao(), banco, versao.splitlines()[0])


def catalogo() -> list[Tabela]:
    with conexao() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema IN ('public', 'pipeline') AND table_type = 'BASE TABLE'
            ORDER BY 1, 2
            """
        )
        tabelas = cur.fetchall()
        resultado = []
        for schema, nome in tabelas:
            cur.execute(f"SELECT COUNT(*) FROM {schema}.{nome}")
            resultado.append(Tabela(schema, nome, cur.fetchone()[0]))
    return resultado


def limpar(confirmar: bool = False) -> list[str]:
    _exigir_confirmacao(confirmar, "db limpar")
    removidas = []
    with conexao() as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS pipeline CASCADE")
        removidas.append("schema pipeline")
        for tabela in reversed(TABELAS_NEGOCIO):
            cur.execute(f"DROP TABLE IF EXISTS public.{tabela} CASCADE")
            removidas.append(f"public.{tabela}")
        conn.commit()
    return removidas


def recriar(confirmar: bool = False, registrar_bronze: bool = False) -> ResultadoRecriar:
    _exigir_confirmacao(confirmar, "db recriar")
    limpar(confirmar=True)

    resultado = ResultadoRecriar()
    for modulo in MODULOS_SCHEMA:
        codigo = subprocess.run([sys.executable, "-m", modulo], cwd=str(caminhos.raiz())).returncode
        if codigo == 0:
            resultado.modulos_ok.append(modulo)
        else:
            resultado.modulos_falhos.append(modulo)

    if registrar_bronze and not resultado.modulos_falhos:
        from loterias.admin.registro_bronze import registrar_bronze as registrar

        registro = registrar()
        resultado.arquivos_bronze = registro.arquivos
        resultado.concursos_enfileirados = registro.concursos_enfileirados

    if not resultado.modulos_falhos:
        resultado.seed = seed()

    return resultado


def seed() -> list[TipoJogoSeed]:
    with conexao() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id_tipo_jogo, codigo, nome_exibicao,
                   dezenas_sorteadas, dezenas_apostadas, dezenas_disponiveis
            FROM public.tipo_jogo ORDER BY id_tipo_jogo
            """
        )
        return [TipoJogoSeed(*linha) for linha in cur.fetchall()]


def bancos() -> list[str]:
    with conexao() as conn, conn.cursor() as cur:
        cur.execute("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname")
        return [linha[0] for linha in cur.fetchall()]


def views_aplicar() -> list[str]:
    sql = caminhos.sql("views_saude.sql").read_text(encoding="utf-8")
    with conexao() as conn, conn.cursor() as cur:
        cur.execute(sql)
        conn.commit()
        cur.execute(
            """
            SELECT table_name FROM information_schema.views
            WHERE table_schema = 'pipeline'
              AND (table_name LIKE 'v_saude_%' OR table_name LIKE 'v_%_detalhe')
            ORDER BY table_name
            """
        )
        return [f"pipeline.{r[0]}" for r in cur.fetchall()]
