from __future__ import annotations

from datetime import date

INICIO_DO_REAL = date(1994, 7, 1)
CRUZEIROS_REAIS_POR_REAL = 2750


def em_reais(valor: float | None, data_apuracao: date | None) -> float | None:
    if valor is None or data_apuracao is None or data_apuracao >= INICIO_DO_REAL:
        return valor
    return round(float(valor) / CRUZEIROS_REAIS_POR_REAL, 2)


def arrecadacao_informada(valor: float | None) -> float | None:
    return valor if valor else None
