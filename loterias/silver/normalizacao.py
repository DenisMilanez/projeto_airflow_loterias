from __future__ import annotations

import re
import unicodedata
from functools import cache

from loterias.config import local_sorteio_canonicos, local_sorteio_invalidos


def chave(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", sem_acento.upper())).strip()


@cache
def _catalogo_local_sorteio() -> dict[str, str | None]:
    catalogo: dict[str, str | None] = {}
    for canonico, variantes in local_sorteio_canonicos().items():
        for nome in (canonico, *variantes):
            catalogo[chave(nome)] = canonico
    for invalido in local_sorteio_invalidos():
        catalogo[chave(invalido)] = None
    return catalogo


def nome_local_sorteio(bruto: str | None) -> str | None:
    texto = (bruto or "").strip()
    if not texto:
        return None
    catalogo = _catalogo_local_sorteio()
    return catalogo[chave(texto)] if chave(texto) in catalogo else texto.upper()


def local_sorteio_conhecido(nome: str) -> bool:
    return nome in local_sorteio_canonicos()
