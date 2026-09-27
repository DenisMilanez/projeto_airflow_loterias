from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg2.extras

from loterias import caminhos
from loterias.db import conexao

OUT = caminhos.docs() / "catalogo_dados.md"

DESCRICOES = {
    "tipo_jogo": "Catalogo das modalidades (Mega-Sena, Quina, etc) e suas regras "
    "(quantas dezenas sao sorteadas, apostadas e disponiveis).",
    "faixa": "Faixas de premiacao de cada modalidade (ex: 6 acertos, 5 acertos...). "
    "O campo 'acertos' e o numero de acertos daquela faixa.",
    "localidade": "Municipio + UF. UF '--' = canal eletronico (compra online, sem cidade fisica).",
    "local_sorteio": "Local fisico onde o sorteio ocorreu (ex: CAMINHAO DA SORTE, ESTUDIO DE TV).",
    "concurso": "Um concurso de uma modalidade. Tabela central — quase tudo se liga a ela. "
    "Traz data de apuracao, se acumulou, valores arrecadados/estimados, etc.",
    "dezena": "Dezenas sorteadas. 1 LINHA POR DEZENA de cada concurso (nao por concurso). "
    "Para jogos com 2 sorteios, 'segundo_sorteio'=true marca as do 2o.",
    "rateio": "Resultado financeiro por faixa de premiacao de cada concurso: "
    "quantos ganhadores e quanto cada um levou.",
    "ganhador_municipio": "De quais municipios sairam os ganhadores de cada concurso "
    "(fonte: API de concursos).",
    "loterica": "Loterica fisica ou canal de venda (nome fantasia, canal de vendas).",
    "ganhador_loterica": "MAIOR TABELA. Ganhadores rastreados a nivel de loterica especifica "
    "(fonte: API 'locais da sorte'). Liga concurso + loterica + faixa.",
}

ORDEM = [
    "tipo_jogo",
    "faixa",
    "localidade",
    "local_sorteio",
    "concurso",
    "dezena",
    "rateio",
    "ganhador_municipio",
    "loterica",
    "ganhador_loterica",
]


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def gerar() -> Path:
    with conexao() as conn:
        return _montar(conn)


def _montar(conn) -> Path:
    cur = conn.cursor()
    dcur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public' AND table_type='BASE TABLE'
    """)
    tabelas = {r[0] for r in cur.fetchall()}
    ordenadas = [t for t in ORDEM if t in tabelas] + sorted(tabelas - set(ORDEM))

    linhas: list[str] = []
    w = linhas.append

    w("# Catalogo de dados do schema `public`\n")
    w("> Gerado automaticamente por `loterias-admin dados catalogo-gerar`.\n")
    w(
        "Camada **Gold** da arquitetura medallion — "
        "dados ja limpos e normalizados, prontos pra analise.\n"
    )

    w("## Visao geral (volumes)\n")
    w("| Tabela | Linhas | Descricao |")
    w("|---|---:|---|")
    counts = {}
    for t in ordenadas:
        cur.execute(f"SELECT COUNT(*) FROM public.{t}")
        counts[t] = cur.fetchone()[0]
        desc = DESCRICOES.get(t, "")
        w(f"| `{t}` | {counts[t]:,} | {desc} |")
    w("")

    w("## Relacionamentos (chaves estrangeiras)\n")
    cur.execute("""
        SELECT tc.table_name, kcu.column_name,
               ccu.table_name AS ref_table, ccu.column_name AS ref_col
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public'
        ORDER BY tc.table_name, kcu.column_name
    """)
    w("```")
    for r in cur.fetchall():
        w(f"{r[0]}.{r[1]}  ->  {r[2]}.{r[3]}")
    w("```\n")

    w("## Detalhe das tabelas\n")
    for t in ordenadas:
        w(f"### `{t}`  ({counts[t]:,} linhas)\n")
        if t in DESCRICOES:
            w(DESCRICOES[t] + "\n")
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable, character_maximum_length
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position
        """,
            (t,),
        )
        w("| Coluna | Tipo | Nulo? |")
        w("|---|---|---|")
        for c in cur.fetchall():
            tipo = c[1] + (f"({c[3]})" if c[3] else "")
            w(f"| `{c[0]}` | {tipo} | {'sim' if c[2] == 'YES' else 'nao'} |")
        dcur.execute(f"SELECT * FROM public.{t} LIMIT 2")
        amostra = [dict(r) for r in dcur.fetchall()]
        w("\n**Amostra:**\n")
        w("```json")
        w(json.dumps(amostra, indent=2, ensure_ascii=False, default=_json_default))
        w("```\n")

    w("## Notas importantes pra analise\n")
    w(
        "- **`dezena` e por dezena**, nao por concurso: pra ter as dezenas de um concurso, "
        "agregue (ex: `array_agg(numero ORDER BY numero)`)."
    )
    w("- **`rateio` e por faixa**: cada concurso tem N linhas (uma por faixa de premiacao).")
    w(
        "- **`ganhador_loterica`** e a maior tabela (~2.8M linhas) — cuidado com full scans; "
        "filtre por `id_concurso`."
    )
    w(
        "- **Canal eletronico** (compra online) aparece como `localidade.uf = '--'` e "
        "`municipio = 'CANAL ELETRONICO'`."
    )
    w("- **`local_sorteio.nome`** e normalizado (UPPER, sem acento, variantes unificadas).")
    w(
        "- Alguns mega-acumulados gigantes tem **coleta parcial** de `ganhador_loterica` "
        "(limite de 100k registros da API da Caixa) — ver `pipeline.v_fila_detalhe` status='parcial'."
    )
    w("")
    w("## Views de saude/exploracao (schema `pipeline`)\n")
    w(
        "Ja existem views prontas: `v_saude_cobertura`, `v_saude_filas`, `v_saude_problemas`, "
        "`v_saude_lacunas`, `v_saude_volumes`, `v_saude_execucoes`, `v_concurso_detalhe`, "
        "`v_fila_detalhe`. Veja `loterias/sql/views_saude.sql`."
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(linhas), encoding="utf-8")
    return OUT
