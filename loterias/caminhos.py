from __future__ import annotations

import os
from pathlib import Path

VARIAVEL_RAIZ = "LOTERIAS_HOME"


def raiz() -> Path:
    definida = os.environ.get(VARIAVEL_RAIZ)
    if definida:
        return Path(definida).resolve()
    return Path(__file__).resolve().parents[1]


def dados() -> Path:
    return raiz() / "data"


def bronze() -> Path:
    return dados() / "bronze"


def registrar(caminho: Path) -> str:
    return Path(caminho).resolve().relative_to(raiz()).as_posix()


def localizar(registrado: str) -> Path:
    normalizado = str(registrado).replace("\\", "/")
    _, marcador, resto = normalizado.partition("data/bronze/")
    if marcador:
        return bronze() / resto
    return raiz() / normalizado


def silver() -> Path:
    return dados() / "silver"


def gold() -> Path:
    return dados() / "gold"


def cache() -> Path:
    return dados() / "_cache"


def config_yaml() -> Path:
    return raiz() / "config" / "loterias.yaml"


def docs() -> Path:
    return raiz() / "docs"


def env_local() -> Path:
    return raiz() / ".env.local"


def sql(nome: str) -> Path:
    return Path(__file__).resolve().parent / "sql" / nome
