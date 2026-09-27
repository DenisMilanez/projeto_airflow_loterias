from __future__ import annotations

REPARAVEL = """
    seguinte.id_tipo_jogo = c.id_tipo_jogo
    AND seguinte.numero_concurso = c.numero_concurso + 1
    AND c.data_proximo_concurso IS DISTINCT FROM seguinte.data_apuracao
    AND (c.data_proximo_concurso IS NULL OR c.data_proximo_concurso <= c.data_apuracao)
"""


def contar_reparaveis(cur) -> int:
    cur.execute(
        f"SELECT COUNT(*) FROM public.concurso c, public.concurso seguinte WHERE {REPARAVEL}"
    )
    return cur.fetchone()[0]


def completar(cur) -> int:
    cur.execute(
        f"""
        UPDATE public.concurso c
        SET data_proximo_concurso = seguinte.data_apuracao
        FROM public.concurso seguinte
        WHERE {REPARAVEL}
        """
    )
    return cur.rowcount
