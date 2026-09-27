from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from loterias import caminhos


@dataclass
class ResultadoBackup:
    origem: Path
    destino: Path
    arquivos: int
    bytes: int
    origem_limpa: bool


def _medir(pasta: Path) -> tuple[int, int]:
    if not pasta.exists():
        return 0, 0
    arquivos = [f for f in pasta.rglob("*") if f.is_file()]
    return len(arquivos), sum(f.stat().st_size for f in arquivos)


def backup(destino: str = "data-backup", limpar_origem: bool = False) -> ResultadoBackup:
    origem = caminhos.dados()
    alvo = caminhos.raiz() / destino
    if not origem.exists():
        raise FileNotFoundError(f"pasta de dados nao encontrada: {origem}")

    arquivos, tamanho = _medir(origem)
    alvo.mkdir(parents=True, exist_ok=True)
    for item in origem.iterdir():
        candidato = alvo / item.name
        if item.is_dir():
            shutil.copytree(item, candidato, dirs_exist_ok=True)
        else:
            shutil.copy2(item, candidato)

    if limpar_origem:
        for item in origem.iterdir():
            shutil.rmtree(item) if item.is_dir() else item.unlink()

    return ResultadoBackup(origem, alvo, arquivos, tamanho, limpar_origem)


def formatar_bytes(n: int) -> str:
    for unidade, divisor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= divisor:
            return f"{n / divisor:.2f} {unidade}"
    return f"{n} B"
