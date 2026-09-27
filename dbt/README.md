# Camada analítica em dbt

O `silver → gold` em Python continua sendo a carga. O dbt entra **depois** do
gold, construindo agregações em cima do schema `public`.

A divisão é proposital: o Python faz o trabalho que exige rede, retry, fila e
estado; o dbt faz o trabalho que é SQL declarativo puro e ganha lineage e
testes de graça.

## Rodar

O pacote traz o dbt como extra:

```bash
pip install -e ".[dbt]"
cd dbt
dbt deps
dbt build
```

O `profiles.yml` lê as mesmas variáveis de ambiente do pipeline
(`LOTERIAS_DB_*`), então não há credencial duplicada. Com o `.env.local`
padrão, aponta para o Postgres do Docker em `localhost:5433`.

Documentação navegável com lineage:

```bash
dbt docs generate && dbt docs serve
```

## Modelos

### Staging (views, schema `analytics_staging`)

| Modelo | O que resolve |
|---|---|
| `stg_concurso` | Junta concurso com tipo_jogo, deriva mês e ano, zera nulos de valor |
| `stg_rateio` | Junta rateio com faixa, trazendo acertos e descrição |
| `stg_ganhador_municipio` | Junta ganhadores com localidade e marca canal eletrônico |

### Marts (tabelas, schema `analytics_marts`)

| Modelo | Grão | Conteúdo |
|---|---|---|
| `mart_resumo_modalidade` | modalidade | Volumetria, arrecadação, prêmios pagos e percentual de retorno |
| `mart_ganhadores_municipio` | uf + município + modalidade | Ganhadores e concursos premiados por cidade |
| `mart_premiacao_faixa` | modalidade + faixa | Prêmio médio e máximo, concursos com e sem ganhador |
| `mart_serie_mensal` | modalidade + mês | Série temporal com arrecadação acumulada em window function |

## Testes

26 testes: `unique`, `not_null`, `relationships` nas sources, e de `dbt_utils`
o `unique_combination_of_columns` no grão de cada mart mais `accepted_range`
nos percentuais.

Rodar só os testes:

```bash
dbt test
```
