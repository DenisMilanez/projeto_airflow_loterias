# Metabase

Painel de saúde do pipeline. Lê as views `pipeline.v_saude_*` do PostgreSQL do
projeto — toda a lógica de "como vai o pipeline?" está versionada em
`loterias/sql/views_saude.sql`, então o Metabase só desenha por cima.

## Subir

O Metabase entra na mesma rede Docker do pipeline, então o stack principal
precisa estar de pé antes:

```bash
docker compose -f airflow/docker-compose.yaml up -d
docker compose -f metabase/docker-compose.yaml up -d
```

Abre em `http://localhost:3000`.

## Conectar no banco

Na primeira execução, o Metabase pede uma conexão. Use:

| Campo | Valor |
|---|---|
| Tipo | PostgreSQL |
| Host | `postgres-loterias` |
| Porta | `5432` |
| Database | `loterias` |
| Usuário | `metabase` |
| Senha | `metabase` |

O usuário `metabase` é somente leitura e é criado automaticamente por
`loterias-admin db views-aplicar`, junto com as permissões nos schemas
`public` e `pipeline`. Se as views ainda não existirem:

```bash
loterias-admin db views-aplicar
```

## Views disponíveis

| View | Para que serve |
|---|---|
| `v_saude_cobertura` | Até onde cada modalidade foi coletada e se está atrasada |
| `v_saude_lacunas` | Concursos faltando no meio da sequência |
| `v_saude_filas` | Contagem por fonte, modalidade e status |
| `v_saude_problemas` | Itens em erro, abandonados ou parciais |
| `v_saude_execucoes` | Histórico das execuções, mais recentes primeiro |
| `v_saude_volumes` | Volumetria das tabelas de negócio |
| `v_concurso_detalhe` | Um concurso com dezenas e rateios agregados |
| `v_fila_detalhe` | Fila item a item, com tentativas e detalhe do erro |

Os dashboards ficam no volume `metabase-metadata` e não vão pro repositório —
quem clonar monta os próprios em cima das mesmas views.
