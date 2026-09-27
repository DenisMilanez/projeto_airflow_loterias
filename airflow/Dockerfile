FROM apache/airflow:2.9.2

COPY --chown=airflow:root pyproject.toml /opt/loterias/pyproject.toml
COPY --chown=airflow:root loterias /opt/loterias/loterias

RUN pip install --no-cache-dir -e /opt/loterias
