from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

NOME_PROJETO = "projeto_airflow_loterias"
REPOSITORIO = "https://github.com/DenisMilanez/projeto_airflow_loterias"
VERSAO_DESCONHECIDA = "0.0.0"

PORTAL_LOTERIAS = {
    "Referer": "https://loterias.caixa.gov.br/",
    "Origin": "https://loterias.caixa.gov.br",
}


def versao_pacote() -> str:
    try:
        return version("loterias")
    except PackageNotFoundError:
        return VERSAO_DESCONHECIDA


def user_agent() -> str:
    return f"Mozilla/5.0 (compatible; {NOME_PROJETO}/{versao_pacote()}; +{REPOSITORIO})"


def cabecalhos(extras: dict[str, str] | None = None) -> dict[str, str]:
    base = {"User-Agent": user_agent(), "Accept": "application/json"}
    if extras:
        base.update(extras)
    return base
