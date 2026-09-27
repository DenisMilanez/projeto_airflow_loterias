from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import yaml

from loterias import caminhos

_CONFIG_PATH = caminhos.config_yaml()

_data: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _data
    if _data is None:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            _data = yaml.safe_load(f)
    return _data


def _jogo(modalidade: str) -> dict[str, Any]:
    d = _load()
    try:
        return d["jogos"][modalidade]
    except KeyError:
        raise KeyError(
            f"Modalidade '{modalidade}' não encontrada em config/loterias.yaml. "
            f"Disponíveis: {list(d.get('jogos', {}).keys())}"
        ) from None


def listar_modalidades() -> list[str]:
    return list(_load().get("jogos", {}).keys())


def id_tipo_jogo(modalidade: str) -> int:
    return int(_jogo(modalidade)["id_tipo_jogo"])


def codigo_api(modalidade: str) -> str:
    return str(_jogo(modalidade)["codigo_api"])


def nome_exibicao(modalidade: str) -> str:
    return str(_jogo(modalidade)["nome_exibicao"])


def dezenas_sorteadas(modalidade: str) -> int:
    return int(_jogo(modalidade)["dezenas_sorteadas"])


def dezenas_apostadas(modalidade: str) -> int:
    return int(_jogo(modalidade)["dezenas_apostadas"])


def dezenas_disponiveis(modalidade: str) -> int:
    return int(_jogo(modalidade)["dezenas_disponiveis"])


def dias_sorteio(modalidade: str) -> list[int]:
    return [int(d) for d in _jogo(modalidade).get("dias_sorteio", [])]


def deveria_ter_sorteio_hoje(modalidade: str, dt: date | None = None) -> bool:
    if dt is None:
        dt = date.today()
    return dt.weekday() in dias_sorteio(modalidade)


def concursos_param(modalidade: str, chave: str, default: Any = None) -> Any:
    cfg = _jogo(modalidade).get("concursos") or {}
    return cfg.get(chave, default)


def locais_sorte_param(modalidade: str, chave: str, default: Any = None) -> Any:
    cfg = _jogo(modalidade).get("locais_sorte") or {}
    return cfg.get(chave, default)


def locais_sorte_concurso_min(modalidade: str) -> int | None:
    v = locais_sorte_param(modalidade, "concurso_min")
    return None if v is None else int(v)


def locais_sorte_request_sleep(modalidade: str) -> float:
    return float(locais_sorte_param(modalidade, "request_sleep", 4.0))


def locais_sorte_pausa_apos_429(modalidade: str) -> float:
    return float(locais_sorte_param(modalidade, "pausa_apos_429_s", 60.0))


def locais_sorte_pausa_apos_403(modalidade: str) -> float:
    return float(locais_sorte_param(modalidade, "pausa_apos_403_s", 60.0))


def locais_sorte_max_retries_inline(modalidade: str) -> int:
    return int(locais_sorte_param(modalidade, "max_retries_inline", 3))


def locais_sorte_batch_parquet(modalidade: str) -> int:
    return int(locais_sorte_param(modalidade, "batch_parquet", 10))


def fila_max_tentativas() -> int:
    return int(_load().get("fila", {}).get("max_tentativas", 10))


def fila_backoff_horas() -> list[int]:
    return [
        int(h)
        for h in _load()
        .get("fila", {})
        .get("backoff_horas_por_tentativa", [6, 12, 24, 48, 48, 72, 72, 96, 96, 168])
    ]


def fila_proximo_retry(tentativas_feitas: int, agora: datetime | None = None) -> datetime:
    if agora is None:
        agora = datetime.now(UTC)
    horas = fila_backoff_horas()
    idx = min(max(tentativas_feitas - 1, 0), len(horas) - 1)
    return agora + timedelta(hours=horas[idx])


def _norm() -> dict[str, Any]:
    return _load().get("normalizacao") or {}


def _canal_eletronico_cfg() -> dict[str, Any]:
    return _norm().get("canal_eletronico") or {}


def canal_eletronico_municipio_canonico() -> str:
    return str(_canal_eletronico_cfg().get("municipio_canonico", "CANAL ELETRONICO"))


def canal_eletronico_uf_canonica() -> str:
    return str(_canal_eletronico_cfg().get("uf_canonica", "--"))


def canal_eletronico_variantes() -> set[str]:
    variantes = _canal_eletronico_cfg().get("variantes_municipio") or []
    return {str(v).upper() for v in variantes}


def _local_sorteio_cfg() -> dict[str, Any]:
    return _norm().get("local_sorteio") or {}


def local_sorteio_canonicos() -> dict[str, list[str]]:
    canonicos = _local_sorteio_cfg().get("canonicos") or {}
    return {str(nome): [str(v) for v in variantes or []] for nome, variantes in canonicos.items()}


def local_sorteio_invalidos() -> list[str]:
    return [str(v) for v in _local_sorteio_cfg().get("invalidos") or []]


def _municipio_cfg() -> dict[str, Any]:
    return _norm().get("municipio") or {}


def municipio_correcoes() -> dict[str, str]:
    return {str(k): str(v) for k, v in (_municipio_cfg().get("correcoes") or {}).items()}


def municipio_similaridade_minima() -> float:
    return float(_municipio_cfg().get("similaridade_minima", 0.8))


def municipio_folga_sobre_segundo() -> float:
    return float(_municipio_cfg().get("folga_sobre_segundo", 0.05))


def concurso_esperado_hoje(
    modalidade: str, ultimo_concurso_db: int | None, dt: date | None = None
) -> int | None:
    if dt is None:
        dt = date.today()
    if not deveria_ter_sorteio_hoje(modalidade, dt):
        return None
    if ultimo_concurso_db is None:
        return None
    return int(ultimo_concurso_db) + 1
