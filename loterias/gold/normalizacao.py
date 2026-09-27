from __future__ import annotations

from dataclasses import dataclass, field

from loterias.silver.ibge import SEM_MUNICIPIO, corrigir_municipio, municipio_oficial
from loterias.silver.normalizacao import local_sorteio_conhecido, nome_local_sorteio

UFS_SEM_MUNICIPIO = ("NA", "--")


@dataclass
class Resultado:
    localidades: int = 0
    locais_sorteio: int = 0
    lotericas_unificadas: int = 0


@dataclass
class Pendencias:
    localidades: int = 0
    locais_sorteio: int = 0
    fora_do_ibge: list[str] = field(default_factory=list)
    locais_fora_do_catalogo: list[str] = field(default_factory=list)


def _localidades_a_corrigir(cur) -> list[tuple[int, str, str]]:
    cur.execute("SELECT id_localidade, municipio, uf FROM public.localidade")
    pendentes = []
    for id_localidade, municipio, uf in cur.fetchall():
        destino = corrigir_municipio(municipio, uf)
        if destino != (municipio, uf.strip()):
            pendentes.append((id_localidade, *destino))
    return pendentes


def _locais_a_corrigir(cur) -> list[tuple[int, str | None]]:
    cur.execute("SELECT id_local_sorteio, nome FROM public.local_sorteio")
    return [
        (i, nome_local_sorteio(nome))
        for i, nome in cur.fetchall()
        if nome_local_sorteio(nome) != nome
    ]


def _mover_local_sorteio(cur, id_local: int, nome: str, id_localidade: int) -> None:
    cur.execute(
        """
        SELECT id_local_sorteio FROM public.local_sorteio
        WHERE nome = %s AND id_localidade = %s AND id_local_sorteio <> %s
        """,
        (nome, id_localidade, id_local),
    )
    existente = cur.fetchone()
    if existente is None:
        cur.execute(
            "UPDATE public.local_sorteio SET nome = %s, id_localidade = %s WHERE id_local_sorteio = %s",
            (nome, id_localidade, id_local),
        )
        return
    cur.execute(
        "UPDATE public.concurso SET id_local_sorteio = %s WHERE id_local_sorteio = %s",
        (existente[0], id_local),
    )
    cur.execute("DELETE FROM public.local_sorteio WHERE id_local_sorteio = %s", (id_local,))


def _mover_loterica(cur, id_loterica: int, id_localidade: int) -> bool:
    cur.execute(
        """
        SELECT destino.id_loterica
        FROM public.loterica origem
        JOIN public.loterica destino
          ON destino.razao_social = origem.razao_social
         AND destino.canal_vendas IS NOT DISTINCT FROM origem.canal_vendas
         AND destino.id_localidade = %s
        WHERE origem.id_loterica = %s
        """,
        (id_localidade, id_loterica),
    )
    existente = cur.fetchone()
    if existente is None:
        cur.execute(
            "UPDATE public.loterica SET id_localidade = %s WHERE id_loterica = %s",
            (id_localidade, id_loterica),
        )
        return False
    cur.execute(
        """
        DELETE FROM public.ganhador_loterica origem
        USING public.ganhador_loterica destino
        WHERE origem.id_loterica = %s AND destino.id_loterica = %s
          AND destino.id_concurso = origem.id_concurso
          AND destino.id_faixa IS NOT DISTINCT FROM origem.id_faixa
          AND destino.tipo_aposta IS NOT DISTINCT FROM origem.tipo_aposta
        """,
        (id_loterica, existente[0]),
    )
    cur.execute(
        "UPDATE public.ganhador_loterica SET id_loterica = %s WHERE id_loterica = %s",
        (existente[0], id_loterica),
    )
    cur.execute("DELETE FROM public.loterica WHERE id_loterica = %s", (id_loterica,))
    return True


def _unificar_localidade(cur, origem: int, destino: int) -> int:
    cur.execute(
        "SELECT id_local_sorteio, nome FROM public.local_sorteio WHERE id_localidade = %s",
        (origem,),
    )
    for id_local, nome in cur.fetchall():
        _mover_local_sorteio(cur, id_local, nome, destino)

    cur.execute("SELECT id_loterica FROM public.loterica WHERE id_localidade = %s", (origem,))
    unificadas = sum(
        _mover_loterica(cur, id_loterica, destino) for (id_loterica,) in cur.fetchall()
    )

    cur.execute(
        """
        DELETE FROM public.ganhador_municipio origem
        USING public.ganhador_municipio destino
        WHERE origem.id_localidade = %s AND destino.id_localidade = %s
          AND destino.id_concurso = origem.id_concurso
          AND destino.posicao IS NOT DISTINCT FROM origem.posicao
        """,
        (origem, destino),
    )
    cur.execute(
        "UPDATE public.ganhador_municipio SET id_localidade = %s WHERE id_localidade = %s",
        (destino, origem),
    )
    cur.execute("DELETE FROM public.localidade WHERE id_localidade = %s", (origem,))
    return unificadas


def normalizar(cur) -> Resultado:
    resultado = Resultado()

    for id_localidade, municipio, uf in _localidades_a_corrigir(cur):
        cur.execute(
            """
            SELECT id_localidade FROM public.localidade
            WHERE municipio = %s AND uf = %s AND id_localidade <> %s
            """,
            (municipio, uf, id_localidade),
        )
        existente = cur.fetchone()
        if existente is None:
            cur.execute(
                "UPDATE public.localidade SET municipio = %s, uf = %s WHERE id_localidade = %s",
                (municipio, uf, id_localidade),
            )
        else:
            resultado.lotericas_unificadas += _unificar_localidade(cur, id_localidade, existente[0])
        resultado.localidades += 1

    for id_local, nome in _locais_a_corrigir(cur):
        if nome is None:
            cur.execute(
                "UPDATE public.concurso SET id_local_sorteio = NULL WHERE id_local_sorteio = %s",
                (id_local,),
            )
            cur.execute("DELETE FROM public.local_sorteio WHERE id_local_sorteio = %s", (id_local,))
        else:
            cur.execute(
                "SELECT id_localidade FROM public.local_sorteio WHERE id_local_sorteio = %s",
                (id_local,),
            )
            _mover_local_sorteio(cur, id_local, nome, cur.fetchone()[0])
        resultado.locais_sorteio += 1

    return resultado


def pendencias(cur) -> Pendencias:
    resultado = Pendencias(
        localidades=len(_localidades_a_corrigir(cur)),
        locais_sorteio=len(_locais_a_corrigir(cur)),
    )
    cur.execute("SELECT municipio, uf FROM public.localidade ORDER BY municipio")
    resultado.fora_do_ibge = [
        f"{municipio}/{uf.strip()}"
        for municipio, uf in cur.fetchall()
        if municipio != SEM_MUNICIPIO
        and uf.strip() not in UFS_SEM_MUNICIPIO
        and not municipio_oficial(municipio, uf)
    ]
    cur.execute("SELECT DISTINCT nome FROM public.local_sorteio ORDER BY nome")
    resultado.locais_fora_do_catalogo = [
        nome for (nome,) in cur.fetchall() if not local_sorteio_conhecido(nome)
    ]
    return resultado
