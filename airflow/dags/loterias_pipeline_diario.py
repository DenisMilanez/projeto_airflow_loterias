from __future__ import annotations

from datetime import datetime, timedelta

from airflow.operators.bash import BashOperator
from airflow.utils.task_group import TaskGroup

from airflow import DAG

PROJETO = "/opt/loterias"
MODALIDADES_PADRAO = ["MEGA_SENA", "QUINA", "LOTOFACIL", "LOTOMANIA"]

try:
    from loterias.config import listar_modalidades

    MODALIDADES = listar_modalidades()
except Exception:
    MODALIDADES = MODALIDADES_PADRAO


def executar(modulo: str, *args: str) -> str:
    extra = (" " + " ".join(args)) if args else ""
    return f"cd {PROJETO} && python -m {modulo}{extra}"


default_args = {
    "owner": "denis",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="loterias_pipeline_diario",
    description="Coleta incremental diaria: bronze -> silver -> gold (concursos + locais da sorte)",
    start_date=datetime(2026, 1, 1),
    schedule="0 3 * * *",
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["loterias", "incremental", "diario"],
) as dag:
    with TaskGroup(group_id="concursos") as g_concursos:
        bronze_concursos = [
            BashOperator(
                task_id=f"bronze_{modalidade}",
                bash_command=executar("loterias.bronze.concursos", "--modalidade", modalidade),
            )
            for modalidade in MODALIDADES
        ]
        for anterior, proximo in zip(bronze_concursos, bronze_concursos[1:], strict=False):
            anterior >> proximo

        silver_concursos = BashOperator(
            task_id="silver",
            bash_command=executar("loterias.silver.concursos"),
        )
        gold_concursos = BashOperator(
            task_id="gold",
            bash_command=executar("loterias.gold.concursos"),
        )
        bronze_concursos[-1] >> silver_concursos >> gold_concursos

    with TaskGroup(group_id="locais_sorte") as g_locais:
        bronze_locais = [
            BashOperator(
                task_id=f"bronze_{modalidade}",
                bash_command=executar("loterias.bronze.locais_sorte", "--modalidade", modalidade),
            )
            for modalidade in MODALIDADES
        ]
        for anterior, proximo in zip(bronze_locais, bronze_locais[1:], strict=False):
            anterior >> proximo

        silver_locais = BashOperator(
            task_id="silver",
            bash_command=executar("loterias.silver.locais_sorte"),
        )
        gold_locais = BashOperator(
            task_id="gold",
            bash_command=executar("loterias.gold.locais_sorte"),
        )
        bronze_locais[-1] >> silver_locais >> gold_locais

    g_concursos >> g_locais
