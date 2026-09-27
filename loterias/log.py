from __future__ import annotations

import sys
from datetime import datetime
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, Exception):
    pass

_modalidade: str = ""
_fonte: str = ""


def set_context(modalidade: str, fonte: str) -> None:
    global _modalidade, _fonte
    _modalidade = modalidade
    _fonte = fonte


def _prefix() -> str:
    ts = datetime.now().strftime("%H:%M:%S")
    if _modalidade and _fonte:
        return f"[{ts}] [{_modalidade}/{_fonte}]"
    if _modalidade:
        return f"[{ts}] [{_modalidade}]"
    return f"[{ts}]"


def log(msg: str) -> None:
    print(f"{_prefix()} {msg}", flush=True)


def evento(nome: str, **kwargs: Any) -> None:
    extras = " ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
    print(f"{_prefix()} {nome} {extras}".rstrip(), flush=True)


def secao(titulo: str) -> None:
    print(f"\n{_prefix()} === {titulo} ===", flush=True)


def aviso(msg: str) -> None:
    print(f"{_prefix()} [!] AVISO: {msg}", flush=True)


def erro(msg: str) -> None:
    print(f"{_prefix()} [X] ERRO: {msg}", flush=True, file=sys.stderr)
