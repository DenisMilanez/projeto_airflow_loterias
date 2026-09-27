from __future__ import annotations

from datetime import datetime

from airflow.operators.bash import BashOperator

from airflow import DAG

PROJETO = "/opt/loterias"


def executar(modulo: str) -> str:
    return f"cd {PROJETO} && python -m {modulo}"


with DAG(
    dag_id="loterias_setup_inicial",
    description="Cria as tabelas de negocio, o schema pipeline e o seed dos tipos de jogo",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["loterias", "setup", "manual"],
) as dag:
    criar_tabelas = BashOperator(
        task_id="criar_tabelas",
        bash_command=executar("loterias.schema.publico"),
    )
    criar_pipeline = BashOperator(
        task_id="criar_schema_pipeline_e_seed",
        bash_command=executar("loterias.schema.observabilidade"),
    )

    criar_tabelas >> criar_pipeline
