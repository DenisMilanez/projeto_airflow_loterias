from __future__ import annotations

from datetime import datetime

from airflow.decorators import task
from airflow.exceptions import AirflowFailException
from airflow.models.param import Param

from airflow import DAG

with DAG(
    dag_id="loterias_admin_saude",
    description="Diagnostico do pipeline, depois que a DAG principal termina. Falha se degradado.",
    start_date=datetime(2026, 1, 1),
    schedule="0 12 * * *",
    catchup=False,
    tags=["loterias", "admin", "saude"],
    params={
        "modalidade": Param(None, type=["null", "string"]),
        "falhar_se_degradado": Param(True, type="boolean"),
    },
) as dag:

    @task.sensor(poke_interval=120, timeout=12 * 3600, mode="reschedule")
    def aguardar_pipeline_diario() -> bool:
        from airflow.models import DagRun
        from airflow.utils.state import DagRunState

        ativas = [
            execucao
            for estado in (DagRunState.QUEUED, DagRunState.RUNNING)
            for execucao in DagRun.find(dag_id="loterias_pipeline_diario", state=estado)
        ]
        return not ativas

    @task
    def diagnosticar(params: dict | None = None) -> dict:
        from loterias.admin.saude import verificar

        params = params or {}
        diagnostico = verificar(params.get("modalidade"))
        resumo = {
            "degradado": diagnostico.degradado,
            "motivos": diagnostico.motivos,
            "cobertura": [
                {
                    "modalidade": c.modalidade,
                    "total_concursos": c.total_concursos,
                    "ultimo_concurso": c.ultimo_concurso,
                    "atraso_dias": c.atraso_dias,
                    "situacao": c.situacao,
                }
                for c in diagnostico.cobertura
            ],
        }
        if diagnostico.degradado and params.get("falhar_se_degradado", True):
            raise AirflowFailException(f"pipeline degradado: {'; '.join(diagnostico.motivos)}")
        return resumo

    aguardar_pipeline_diario() >> diagnosticar()
