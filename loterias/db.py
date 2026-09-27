from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg2
from dotenv import load_dotenv
from psycopg2.pool import ThreadedConnectionPool

from loterias.caminhos import env_local

load_dotenv(env_local())


PERFIL_LOCAL = "local"
PERFIL_GERENCIADO = "gerenciado"

_pool: ThreadedConnectionPool | None = None


class ConfiguracaoInvalida(RuntimeError):
    pass


def perfil() -> str:
    escolhido = os.environ.get("LOTERIAS_DB_PERFIL", PERFIL_LOCAL).strip().lower()
    if escolhido not in (PERFIL_LOCAL, PERFIL_GERENCIADO):
        raise ConfiguracaoInvalida(
            f"LOTERIAS_DB_PERFIL='{escolhido}' invalido; use '{PERFIL_LOCAL}' ou '{PERFIL_GERENCIADO}'"
        )
    return escolhido


def _obrigatoria(nome: str) -> str:
    valor = os.environ.get(nome)
    if not valor:
        raise ConfiguracaoInvalida(f"variavel {nome} nao definida no ambiente ou no .env.local")
    return valor


def ssl_mode() -> str:
    definido = os.environ.get("LOTERIAS_DB_SSL_MODE", "").strip()
    if definido:
        return definido
    return "disable" if perfil() == PERFIL_LOCAL else "require"


def caminho_ca() -> Path | None:
    definido = os.environ.get("LOTERIAS_DB_CA", "").strip()
    if not definido:
        return None
    caminho = Path(definido).expanduser()
    return caminho if caminho.is_file() else None


def build_uri(dbname: str | None = None) -> str:
    uri = os.environ.get("LOTERIAS_DB_URI", "").strip()
    if uri:
        if uri.startswith("postgres://"):
            uri = "postgresql://" + uri[len("postgres://") :]
        if dbname:
            partes = urlparse(uri)
            uri = urlunparse(partes._replace(path=f"/{dbname}"))
        return uri
    host = _obrigatoria("LOTERIAS_DB_HOST")
    porta = _obrigatoria("LOTERIAS_DB_PORT")
    usuario = _obrigatoria("LOTERIAS_DB_USUARIO")
    senha = _obrigatoria("LOTERIAS_DB_SENHA")
    banco = dbname or os.environ.get("LOTERIAS_DB_NOME", "postgres")
    return f"postgresql://{usuario}:{senha}@{host}:{porta}/{banco}?sslmode={ssl_mode()}"


def _parametros_ssl() -> dict:
    ca = caminho_ca()
    return {"sslrootcert": str(ca)} if ca else {}


def connect(dbname: str | None = None):
    return psycopg2.connect(build_uri(dbname), **_parametros_ssl())


def pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        minimo = int(os.environ.get("LOTERIAS_DB_POOL_MIN", "1"))
        maximo = int(os.environ.get("LOTERIAS_DB_POOL_MAX", "8"))
        _pool = ThreadedConnectionPool(minimo, maximo, build_uri(), **_parametros_ssl())
    return _pool


def emprestar():
    return pool().getconn()


def devolver(conn) -> None:
    pool().putconn(conn)


@contextmanager
def conexao() -> Iterator:
    origem = pool()
    conn = origem.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        origem.putconn(conn)


@contextmanager
def cursor(commit: bool = True) -> Iterator:
    with conexao() as conn:
        with conn.cursor() as cur:
            yield cur
        if commit:
            conn.commit()


def encerrar_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


def descrever_conexao() -> str:
    partes = urlparse(build_uri())
    return f"perfil={perfil()} host={partes.hostname} porta={partes.port} banco={(partes.path or '/').lstrip('/')} sslmode={ssl_mode()}"
