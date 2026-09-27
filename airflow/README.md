# Airflow

Orquestração do pipeline. LocalExecutor, sem Redis nem workers separados.

## Subir

```bash
docker compose -f airflow/docker-compose.yaml up -d
```

Interface em `http://localhost:8081` (`admin` / `admin`). As DAGs nascem
pausadas — ligue no botão de cada uma.

## Serviços

| Serviço | Porta | Papel |
|---|---|---|
| `postgres-loterias` | 5433 | Os dados do pipeline. É o banco de negócio |
| `postgres-airflow` | — | Metadados do Airflow. Não exposto |
| `scheduler` | — | Decide o que roda e quando |
| `webserver` | 8081 | Interface web |
| `airflow-init` | — | Roda uma vez: migra o banco e cria o usuário admin |

O projeto inteiro é montado em `/opt/loterias` dentro dos containers, e o
pacote é instalado com `pip install -e` na construção da imagem — por isso
editar um arquivo no host reflete na próxima execução da task, sem rebuild.

## DAGs

| DAG | Agenda |
|---|---|
| `loterias_pipeline_diario` | 03:00 UTC |
| `loterias_admin_saude` | 12:00 UTC |
| `loterias_setup_inicial` | manual |
| `loterias_admin_fila_resetar` | manual |

As duas de administração usam `params`, então o formulário aparece em
"Trigger DAG w/ config" com os campos já validados.

## Verificar que as DAGs carregam

```bash
docker compose -f airflow/docker-compose.yaml exec scheduler \
  python /opt/loterias/scripts/checar_dags.py
```

O mesmo script roda no CI.

## Reconstruir a imagem

Necessário só quando mudam as dependências do `pyproject.toml`:

```bash
docker compose -f airflow/docker-compose.yaml build
docker compose -f airflow/docker-compose.yaml up -d
```
