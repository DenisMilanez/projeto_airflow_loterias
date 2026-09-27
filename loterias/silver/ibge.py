from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path

import requests

from loterias.config import (
    municipio_correcoes,
    municipio_folga_sobre_segundo,
    municipio_similaridade_minima,
)

IBGE_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "_cache"
CACHE_FILE = CACHE_DIR / "ibge_municipios.json"
HTTP_TIMEOUT = 30


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"\s+", " ", s).upper().strip()
    return s


def _extrai_uf(m: dict) -> str | None:
    try:
        return m["microrregiao"]["mesorregiao"]["UF"]["sigla"]
    except (TypeError, KeyError):
        pass
    try:
        return m["regiao-imediata"]["regiao-intermediaria"]["UF"]["sigla"]
    except (TypeError, KeyError):
        return None


def _baixar_ibge() -> list[dict]:
    r = requests.get(IBGE_URL, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _carregar(usar_cache: bool = True) -> list[dict]:
    if usar_cache and CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    data = _baixar_ibge()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


_indice: dict[str, list[str]] | None = None


def _build_indice() -> dict[str, list[str]]:
    data = _carregar()
    idx: dict[str, list[str]] = defaultdict(list)
    for m in data:
        uf = _extrai_uf(m)
        if uf:
            idx[_norm(m["nome"])].append(uf)
    return idx


def _get_indice() -> dict[str, list[str]]:
    global _indice
    if _indice is None:
        _indice = _build_indice()
    return _indice


def lookup_uf(municipio: str, prefixo_uf: str | None = None) -> str | None:
    if not municipio:
        return None
    candidatos = _get_indice().get(_norm(municipio), [])
    if prefixo_uf:
        prefixo_uf = prefixo_uf.upper().strip()
        candidatos = [u for u in candidatos if u.startswith(prefixo_uf)]
    if len(candidatos) == 1:
        return candidatos[0]
    return None


SEM_MUNICIPIO = "NAO INFORMADO"


def _nomes_por_uf() -> dict[str, set[str]]:
    return _inverter(id(_get_indice()))


@cache
def _inverter(_versao: int) -> dict[str, set[str]]:
    por_uf: dict[str, set[str]] = defaultdict(set)
    for nome, ufs in _get_indice().items():
        for uf in ufs:
            por_uf[uf].add(nome)
    return por_uf


def _limpar(nome: str) -> str:
    nome = re.sub(r"\s*,\s*[A-Z]{2}$", "", re.sub(r"[.,;]+$", "", nome)).strip()
    nome = re.sub(r"^STO\.?\s", "SANTO ", nome)
    nome = re.sub(r"^S\.\s", "SAO ", nome)
    nome = re.sub(r"\s+(?:D|DO|DE)\s*OESTE$|\s+DOESTE$", " D'OESTE", nome)
    nome = re.sub(r"\bD\s+([AEIOU])", r"D'\1", nome)
    return re.sub(r"'\s+", "'", nome.replace("-", " "))


def _mais_parecido(candidatos: set[str], oficiais: set[str]) -> str | None:
    notas = {
        oficial: max(SequenceMatcher(None, c, oficial).ratio() for c in candidatos)
        for oficial in oficiais
    }
    ranking = sorted(notas.items(), key=lambda item: item[1], reverse=True)
    melhor, nota = ranking[0]
    segunda = ranking[1][1] if len(ranking) > 1 else 0.0
    if (
        nota >= municipio_similaridade_minima()
        and nota - segunda >= municipio_folga_sobre_segundo()
    ):
        return melhor
    return None


@cache
def _corrigir(nome: str, uf: str, _versao: int) -> tuple[str, str]:
    manual = municipio_correcoes().get(f"{nome}/{uf}")
    if manual:
        destino, uf_destino = manual.rsplit("/", 1)
        return destino, uf_destino
    oficiais = _nomes_por_uf().get(uf)
    if not oficiais or nome == SEM_MUNICIPIO or nome in oficiais:
        return nome, uf
    limpo = _limpar(nome)
    if limpo in oficiais:
        return limpo, uf
    return (_mais_parecido({nome, limpo}, oficiais) or nome), uf


def corrigir_municipio(municipio: str, uf: str) -> tuple[str, str]:
    nome, uf = _norm(municipio), (uf or "").upper().strip()
    try:
        versao = id(_get_indice())
    except Exception:
        return nome, uf
    return _corrigir(nome, uf, versao)


def municipio_oficial(municipio: str, uf: str) -> bool:
    return _norm(municipio) in _nomes_por_uf().get((uf or "").strip(), set())


if __name__ == "__main__":
    for cidade, pref in [
        ("Santa Helena de Goiás", "G"),
        ("Fortaleza", "C"),
        ("São Paulo", None),
        ("Bom Jesus", None),
        ("Bom Jesus", "P"),
        ("Cidade Que Nao Existe", None),
    ]:
        print(f"{cidade!r:35} pref={pref!r:5} -> {lookup_uf(cidade, pref)!r}")
