from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from loterias import caminhos
from loterias.config import (
    canal_eletronico_municipio_canonico,
    canal_eletronico_uf_canonica,
    canal_eletronico_variantes,
    listar_modalidades,
)
from loterias.config import (
    dezenas_apostadas as cfg_dezenas_apostadas,
)
from loterias.config import (
    dezenas_disponiveis as cfg_dezenas_disponiveis,
)
from loterias.config import (
    dezenas_sorteadas as cfg_dezenas_sorteadas,
)
from loterias.config import (
    id_tipo_jogo as cfg_id_tipo_jogo,
)
from loterias.config import (
    nome_exibicao as cfg_nome_exibicao,
)
from loterias.db import conexao
from loterias.log import aviso, evento, log, secao, set_context
from loterias.silver.ibge import corrigir_municipio
from loterias.silver.ibge import lookup_uf as _ibge_lookup_uf
from loterias.silver.normalizacao import nome_local_sorteio

_CANAL_ELETRONICO_VARIANTES: set[str] = canal_eletronico_variantes()
_CANAL_ELETRONICO_MUN: str = canal_eletronico_municipio_canonico()
_CANAL_ELETRONICO_UF: str = canal_eletronico_uf_canonica()

STATUS_PENDENTE = "pendente"
STATUS_PROCESSANDO = "processando"
STATUS_CONCLUIDO = "concluido"
STATUS_ERRO = "erro"


def none_if_zero(v: Any) -> int | None:
    if v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return None if n == 0 else n


def _strip_accents(s: str) -> str:
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")


def parse_data_br(s: str | None) -> date | None:
    if not s or not str(s).strip():
        return None
    try:
        d, m, y = str(s).strip().split("/")
        return date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


def _resolver_uf(municipio: str, uf_bruta: str) -> str | None:
    uf = (uf_bruta or "").upper().strip()[:2]
    if len(uf) == 2 and uf.isalpha():
        return uf
    if not municipio or "DIGITAL" in municipio:
        return None
    prefixo = uf if uf.isalpha() else None
    return _ibge_lookup_uf(municipio, prefixo)


def parse_municipio_uf(texto: str | None) -> tuple[str, str] | None:
    if not texto or not str(texto).strip():
        return None
    partes = [p.strip() for p in str(texto).rsplit(",", 1)]
    if len(partes) != 2 or not partes[0] or not partes[1]:
        return None
    mun = _strip_accents(re.sub(r"\s+", " ", partes[0]).upper())
    uf = _resolver_uf(mun, partes[1])
    if not uf:
        return None
    return corrigir_municipio(mun, uf)


def parse_acertos(descricao: str | None) -> int | None:
    if not descricao:
        return None
    m = re.search(r"(\d+)\s*acertos", str(descricao), re.I)
    return int(m.group(1)) if m else None


def int_dezena(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def bronze_to_silver_dir(bronze_path: Path) -> Path:
    rel = bronze_path.resolve().relative_to((caminhos.bronze()).resolve())
    return caminhos.silver() / rel.parent / bronze_path.stem


def data_proximo_concurso_vazia(payload: dict) -> bool:
    return not str(payload.get("dataProximoConcurso") or "").strip()


def preencher_data_proximo_concurso_historico(payloads: list[dict]) -> int:
    if len(payloads) < 2:
        return 0
    ordenados = sorted(payloads, key=lambda p: (str(p.get("tipoJogo") or ""), int(p["numero"])))
    preenchidos = 0
    for i in range(len(ordenados) - 1):
        atual = ordenados[i]
        if not data_proximo_concurso_vazia(atual):
            continue
        prox = ordenados[i + 1]
        if prox.get("tipoJogo") != atual.get("tipoJogo"):
            continue
        if int(prox["numero"]) != int(atual["numero"]) + 1:
            continue
        apur_prox = str(prox.get("dataApuracao") or "").strip()
        if apur_prox:
            atual["dataProximoConcurso"] = apur_prox
            preenchidos += 1
    return preenchidos


def _detectar_modalidade_por_payload(payloads: list[dict]) -> str:
    if not payloads:
        return "MEGA_SENA"
    raw = str(payloads[0].get("tipoJogo") or "").upper()
    if raw in listar_modalidades():
        return raw
    raw_norm = raw.replace("-", "_").replace(" ", "_")
    if raw_norm in listar_modalidades():
        return raw_norm
    return "MEGA_SENA"


def transform_payloads(payloads: list[dict]) -> dict[str, pd.DataFrame]:
    modalidade = _detectar_modalidade_por_payload(payloads)
    tipo_jogo = pd.DataFrame(
        [
            {
                "id_tipo_jogo": cfg_id_tipo_jogo(modalidade),
                "codigo": modalidade,
                "nome_exibicao": cfg_nome_exibicao(modalidade),
                "dezenas_sorteadas": cfg_dezenas_sorteadas(modalidade),
                "dezenas_apostadas": cfg_dezenas_apostadas(modalidade),
                "dezenas_disponiveis": cfg_dezenas_disponiveis(modalidade),
            }
        ]
    )

    faixas_map: dict[tuple[int, str], dict] = {}
    localidades_map: dict[tuple[str, str], dict] = {}
    local_sorteio_map: dict[tuple[str, str, str], dict] = {}
    concursos: list[dict] = []
    dezenas: list[dict] = []
    rateios: list[dict] = []
    ganhadores: list[dict] = []

    for p in payloads:
        num = int(p["numero"])
        codigo = modalidade

        mun_sorteio, uf_sorteio = (None, None)
        parsed = parse_municipio_uf(p.get("nomeMunicipioUFSorteio"))
        if parsed:
            mun_sorteio, uf_sorteio = parsed
            localidades_map[(mun_sorteio, uf_sorteio)] = {
                "municipio": mun_sorteio,
                "uf": uf_sorteio,
            }

        nome_local = nome_local_sorteio(p.get("localSorteio"))
        if nome_local and mun_sorteio and uf_sorteio:
            local_sorteio_map[(nome_local, mun_sorteio, uf_sorteio)] = {
                "nome": nome_local,
                "municipio": mun_sorteio,
                "uf": uf_sorteio,
            }

        for item in p.get("listaRateioPremio") or []:
            nf = int(item["faixa"])
            desc = item.get("descricaoFaixa")
            acertos = parse_acertos(desc)
            key = (nf, codigo)
            if key not in faixas_map:
                faixas_map[key] = {
                    "codigo_tipo_jogo": codigo,
                    "numero_faixa": nf,
                    "descricao": desc,
                    "acertos": acertos if acertos is not None else 0,
                }
            rateios.append(
                {
                    "numero_concurso": num,
                    "codigo_tipo_jogo": codigo,
                    "numero_faixa": nf,
                    "numero_ganhadores": item.get("numeroDeGanhadores"),
                    "valor_premio": item.get("valorPremio"),
                    "valor_total": (
                        (item.get("valorPremio") or 0) * (item.get("numeroDeGanhadores") or 0)
                        if item.get("numeroDeGanhadores")
                        else item.get("valorPremio")
                    ),
                }
            )

        ordem_sorteio_map: dict[int, int] = {}
        for i, d in enumerate(p.get("dezenasSorteadasOrdemSorteio") or [], start=1):
            n = int_dezena(d)
            if n is not None:
                ordem_sorteio_map[n] = i

        lista = [int_dezena(x) for x in (p.get("listaDezenas") or [])]
        lista = [x for x in lista if x is not None]
        lista_crescente = sorted(lista)
        ordem_crescente_map = {n: i + 1 for i, n in enumerate(lista_crescente)}

        for n in lista:
            dezenas.append(
                {
                    "numero_concurso": num,
                    "codigo_tipo_jogo": codigo,
                    "numero": n,
                    "ordem_sorteio": ordem_sorteio_map.get(n),
                    "ordem_crescente": ordem_crescente_map.get(n),
                    "segundo_sorteio": False,
                }
            )

        seg_list = p.get("listaDezenasSegundoSorteio")
        if seg_list:
            for n_raw in seg_list:
                n = int_dezena(n_raw)
                if n is None:
                    continue
                dezenas.append(
                    {
                        "numero_concurso": num,
                        "codigo_tipo_jogo": codigo,
                        "numero": n,
                        "ordem_sorteio": None,
                        "ordem_crescente": None,
                        "segundo_sorteio": True,
                    }
                )

        for g in p.get("listaMunicipioUFGanhadores") or []:
            mun = _strip_accents((g.get("municipio") or "").strip().upper()) or "NAO INFORMADO"
            if mun in _CANAL_ELETRONICO_VARIANTES:
                mun = _CANAL_ELETRONICO_MUN
                uf = _CANAL_ELETRONICO_UF
            else:
                uf = _resolver_uf(mun, g.get("uf") or "") or "NA"
                mun, uf = corrigir_municipio(mun, uf)
            localidades_map[(mun, uf)] = {"municipio": mun, "uf": uf}
            ganhadores.append(
                {
                    "numero_concurso": num,
                    "codigo_tipo_jogo": codigo,
                    "municipio": mun,
                    "uf": uf,
                    "numero_ganhadores": g.get("ganhadores"),
                    "posicao": g.get("posicao"),
                    "nome_fantasia_ul": g.get("nomeFatansiaUL") or g.get("nomeFantasiaUL"),
                    "serie": g.get("serie"),
                }
            )

        concursos.append(
            {
                "numero_concurso": num,
                "codigo_tipo_jogo": codigo,
                "data_apuracao": parse_data_br(p.get("dataApuracao")),
                "data_proximo_concurso": parse_data_br(p.get("dataProximoConcurso")),
                "numero_concurso_anterior": none_if_zero(p.get("numeroConcursoAnterior")),
                "numero_concurso_proximo": none_if_zero(p.get("numeroConcursoProximo")),
                "numero_concurso_final_0_5": none_if_zero(p.get("numeroConcursoFinal_0_5")),
                "local_sorteio_nome": nome_local,
                "localidade_municipio": mun_sorteio,
                "localidade_uf": uf_sorteio,
                "acumulado": bool(p.get("acumulado")),
                "ultimo_concurso": bool(p.get("ultimoConcurso")),
                "indicador_concurso_especial": p.get("indicadorConcursoEspecial"),
                "tipo_publicacao": p.get("tipoPublicacao"),
                "numero_jogo": p.get("numeroJogo"),
                "observacao": p.get("observacao"),
                "valor_arrecadado": p.get("valorArrecadado"),
                "valor_estimado_proximo_concurso": p.get("valorEstimadoProximoConcurso"),
                "valor_acumulado_proximo_concurso": p.get("valorAcumuladoProximoConcurso"),
                "valor_acumulado_concurso_especial": p.get("valorAcumuladoConcursoEspecial"),
                "valor_acumulado_concurso_0_5": p.get("valorAcumuladoConcurso_0_5"),
                "valor_saldo_reserva_garantidora": p.get("valorSaldoReservaGarantidora"),
                "valor_total_premio_faixa_um": p.get("valorTotalPremioFaixaUm"),
            }
        )

    return {
        "tipo_jogo": tipo_jogo,
        "faixa": pd.DataFrame(list(faixas_map.values())),
        "localidade": pd.DataFrame(list(localidades_map.values())),
        "local_sorteio": pd.DataFrame(list(local_sorteio_map.values())),
        "concurso": pd.DataFrame(concursos),
        "dezena": pd.DataFrame(dezenas),
        "rateio": pd.DataFrame(rateios),
        "ganhador_municipio": pd.DataFrame(ganhadores),
    }


def reset_processando_orfaos(fonte: str = "concursos") -> int:
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pipeline.execucao_arquivo
                SET status_prata = 'pendente'
                WHERE fonte = %s AND status_prata = 'processando'
                """,
                (fonte,),
            )
            n = cur.rowcount
        conn.commit()
        return n


def fetch_arquivos_pendentes(modalidade: str | None = None) -> list[tuple[int, str, int, int, str]]:
    with conexao() as conn:
        with conn.cursor() as cur:
            if modalidade:
                cur.execute(
                    """
                    SELECT ea.id_arquivo, ea.caminho_arquivo, ea.concurso_inicio, ea.concurso_fim,
                           e.tipo
                    FROM pipeline.execucao_arquivo ea
                    JOIN pipeline.execucao e ON e.id_execucao = ea.id_execucao
                    WHERE ea.status_prata IN (%s, %s)
                      AND ea.fonte = 'concursos'
                      AND e.modalidade = %s
                    ORDER BY ea.concurso_inicio
                    """,
                    (STATUS_PENDENTE, STATUS_ERRO, modalidade),
                )
            else:
                cur.execute(
                    """
                    SELECT ea.id_arquivo, ea.caminho_arquivo, ea.concurso_inicio, ea.concurso_fim,
                           e.tipo
                    FROM pipeline.execucao_arquivo ea
                    JOIN pipeline.execucao e ON e.id_execucao = ea.id_execucao
                    WHERE ea.status_prata IN (%s, %s)
                      AND ea.fonte = 'concursos'
                    ORDER BY ea.concurso_inicio
                    """,
                    (STATUS_PENDENTE, STATUS_ERRO),
                )
            return list(cur.fetchall())


def carregar_payloads_bronze(caminho: str) -> list[dict]:
    bronze_path = Path(caminho)
    if not bronze_path.is_file():
        raise FileNotFoundError(f"Bronze nao encontrado: {bronze_path}")
    df = pd.read_parquet(bronze_path)
    return [json.loads(row["payload"]) for _, row in df.iterrows()]


def update_status_arquivo(
    id_arquivo: int,
    status: str,
    *,
    erro: str | None = None,
    processado: bool = False,
) -> None:
    with conexao() as conn:
        with conn.cursor() as cur:
            if processado:
                cur.execute(
                    """
                    UPDATE pipeline.execucao_arquivo
                    SET status_prata = %s,
                        processado_em = %s,
                        erro_prata = %s
                    WHERE id_arquivo = %s
                    """,
                    (status, datetime.now(UTC), erro, id_arquivo),
                )
            else:
                cur.execute(
                    """
                    UPDATE pipeline.execucao_arquivo
                    SET status_prata = %s, erro_prata = %s
                    WHERE id_arquivo = %s
                    """,
                    (status, erro, id_arquivo),
                )
        conn.commit()


def processar_arquivo(id_arquivo: int, caminho: str, payloads: list[dict] | None = None) -> None:
    bronze_path = Path(caminho)
    if not bronze_path.is_file():
        raise FileNotFoundError(f"Bronze nao encontrado: {bronze_path}")

    log(f"lendo {bronze_path.name} ...")
    if payloads is None:
        payloads = carregar_payloads_bronze(caminho)

    tabelas = transform_payloads(payloads)
    out_dir = bronze_to_silver_dir(bronze_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    for nome, tdf in tabelas.items():
        dest = out_dir / f"{nome}.parquet"
        if tdf.empty and nome not in ("tipo_jogo",):
            evento("silver_skip", tabela=nome, motivo="vazio")
        else:
            tdf.to_parquet(dest, engine="pyarrow", index=False)
            evento("silver_salvo", tabela=f"{nome}.parquet", linhas=len(tdf))


def processar_lote(
    id_arquivo: int,
    caminho: str,
    ci: int,
    cf: int,
    tipo: str,
    payloads: list[dict] | None,
) -> None:
    secao(f"silver arquivo id={id_arquivo} concursos {ci}-{cf} ({tipo})")
    update_status_arquivo(id_arquivo, STATUS_PROCESSANDO)
    try:
        processar_arquivo(id_arquivo, caminho, payloads=payloads)
        update_status_arquivo(id_arquivo, STATUS_CONCLUIDO, processado=True, erro=None)
        evento("silver_ok", id_arquivo=id_arquivo, concursos=f"{ci}-{cf}")
    except Exception as e:
        msg = str(e)[:2000]
        aviso(f"silver id_arquivo={id_arquivo}: {msg}")
        update_status_arquivo(id_arquivo, STATUS_ERRO, processado=True, erro=msg)
        raise


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bronze concursos -> Silver.")
    p.add_argument(
        "--modalidade", default=None, help="Opcional: filtrar por modalidade (ex: MEGA_SENA)"
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    set_context(args.modalidade or "TODOS", "silver.concursos")

    orfaos = reset_processando_orfaos("concursos")
    if orfaos:
        log(f"resetados {orfaos} arquivo(s) 'processando' orfaos -> 'pendente'")

    pendentes = fetch_arquivos_pendentes(args.modalidade)
    if not pendentes:
        log("nenhum arquivo bronze pendente para Prata")
        return 0

    secao(f"SILVER CONCURSOS - {len(pendentes)} arquivo(s)")
    historico = [p for p in pendentes if p[4] == "historico"]
    outros = [p for p in pendentes if p[4] != "historico"]
    erros = 0

    if historico:
        log(f"carga historica: {len(historico)} arquivo(s) - preenchendo dataProximoConcurso vazia")
        lotes: list[tuple[int, str, int, int, list[dict]]] = []
        todos_payloads: list[dict] = []
        for id_arquivo, caminho, ci, cf, _tipo in historico:
            payloads = carregar_payloads_bronze(caminho)
            lotes.append((id_arquivo, caminho, ci, cf, payloads))
            todos_payloads.extend(payloads)
        n = preencher_data_proximo_concurso_historico(todos_payloads)
        log(f"dataProximoConcurso preenchidas a partir do concurso seguinte: {n}")
        for id_arquivo, caminho, ci, cf, payloads in lotes:
            try:
                processar_lote(id_arquivo, caminho, ci, cf, "historico", payloads)
            except Exception:
                erros += 1

    for id_arquivo, caminho, ci, cf, tipo in outros:
        try:
            processar_lote(id_arquivo, caminho, ci, cf, tipo, payloads=None)
        except Exception:
            erros += 1

    secao("RESUMO")
    evento("fim", sucesso=len(pendentes) - erros, erros=erros)
    return 1 if erros else 0


if __name__ == "__main__":
    raise SystemExit(main())
