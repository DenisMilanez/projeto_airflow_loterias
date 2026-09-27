from __future__ import annotations

from datetime import datetime

from airflow.decorators import task
from airflow.models.param import Param

from airflow import DAG

with DAG(
    dag_id="loterias_admin_fila_resetar",
    description="Devolve itens travados da fila para 'pendente'. Disparo manual com formulario.",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["loterias", "admin", "manual"],
    params={
        "de": Param("erro", type="string", enum=["erro", "processando", "abandonado"]),
        "fonte": Param("locais_sorte", type="string", enum=["concursos", "locais_sorte"]),
        "modalidade": Param(
            None, type=["null", "string"], description="Deixe vazio e marque 'todas'"
        ),
        "todas": Param(False, type="boolean"),
        "dias": Param(None, type=["null", "integer"], minimum=0),
        "zerar_tentativas": Param(True, type="boolean"),
        "dry_run": Param(True, type="boolean"),
    },
) as dag:

    @task
    def resetar(params: dict | None = None) -> dict:
        from loterias.admin.fila import resetar as resetar_fila

        params = params or {}
        resultado = resetar_fila(
            de=params["de"],
            fonte=params["fonte"],
            modalidade=params.get("modalidade"),
            todas=params.get("todas", False),
            dias=params.get("dias"),
            zerar_tentativas=params.get("zerar_tentativas", True),
            dry_run=params.get("dry_run", True),
        )
        return {
            "afetados": resultado.afetados,
            "dry_run": resultado.dry_run,
            "modalidades": resultado.modalidades,
            "fonte": resultado.fonte,
            "antes": resultado.antes,
            "depois": resultado.depois,
        }

    resetar()
