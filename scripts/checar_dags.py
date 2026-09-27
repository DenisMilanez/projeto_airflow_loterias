from __future__ import annotations

import sys
from pathlib import Path

PASTA_DAGS = Path(__file__).resolve().parent.parent / "airflow" / "dags"


def main() -> int:
    from airflow.models import DagBag

    saco = DagBag(dag_folder=str(PASTA_DAGS), include_examples=False)

    if saco.import_errors:
        print(f"{len(saco.import_errors)} DAG(s) falharam ao importar:", file=sys.stderr)
        for arquivo, erro in saco.import_errors.items():
            print(f"\n  {arquivo}\n    {erro}", file=sys.stderr)
        return 1

    if not saco.dags:
        print(f"nenhuma DAG encontrada em {PASTA_DAGS}", file=sys.stderr)
        return 1

    print(f"{len(saco.dags)} DAG(s) importadas sem erro:")
    for dag_id in sorted(saco.dags):
        tarefas = len(saco.dags[dag_id].tasks)
        print(f"  {dag_id}  ({tarefas} task(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
