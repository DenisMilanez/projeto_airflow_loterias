# Catalogo de dados do schema `public`

> Gerado automaticamente por `loterias-admin dados catalogo-gerar`.

Camada **Gold** da arquitetura medallion — dados ja limpos e normalizados, prontos pra analise.

## Visao geral (volumes)

| Tabela | Linhas | Descricao |
|---|---:|---|
| `tipo_jogo` | 4 | Catalogo das modalidades (Mega-Sena, Quina, etc) e suas regras (quantas dezenas sao sorteadas, apostadas e disponiveis). |
| `faixa` | 19 | Faixas de premiacao de cada modalidade (ex: 6 acertos, 5 acertos...). O campo 'acertos' e o numero de acertos daquela faixa. |
| `localidade` | 5,590 | Municipio + UF. UF '--' = canal eletronico (compra online, sem cidade fisica). |
| `local_sorteio` | 694 | Local fisico onde o sorteio ocorreu (ex: CAMINHAO DA SORTE, ESTUDIO DE TV). |
| `concurso` | 16,958 | Um concurso de uma modalidade. Tabela central — quase tudo se liga a ela. Traz data de apuracao, se acumulou, valores arrecadados/estimados, etc. |
| `dezena` | 170,442 | Dezenas sorteadas. 1 LINHA POR DEZENA de cada concurso (nao por concurso). Para jogos com 2 sorteios, 'segundo_sorteio'=true marca as do 2o. |
| `rateio` | 72,171 | Resultado financeiro por faixa de premiacao de cada concurso: quantos ganhadores e quanto cada um levou. |
| `ganhador_municipio` | 16,227 | De quais municipios sairam os ganhadores de cada concurso (fonte: API de concursos). |
| `loterica` | 48,957 | Loterica fisica ou canal de venda (nome fantasia, canal de vendas). |
| `ganhador_loterica` | 3,054,637 | MAIOR TABELA. Ganhadores rastreados a nivel de loterica especifica (fonte: API 'locais da sorte'). Liga concurso + loterica + faixa. |

## Relacionamentos (chaves estrangeiras)

```
concurso.id_local_sorteio  ->  local_sorteio.id_local_sorteio
concurso.id_tipo_jogo  ->  tipo_jogo.id_tipo_jogo
dezena.id_concurso  ->  concurso.id_concurso
faixa.id_tipo_jogo  ->  tipo_jogo.id_tipo_jogo
ganhador_loterica.id_concurso  ->  concurso.id_concurso
ganhador_loterica.id_faixa  ->  faixa.id_faixa
ganhador_loterica.id_loterica  ->  loterica.id_loterica
ganhador_municipio.id_concurso  ->  concurso.id_concurso
ganhador_municipio.id_localidade  ->  localidade.id_localidade
local_sorteio.id_localidade  ->  localidade.id_localidade
loterica.id_localidade  ->  localidade.id_localidade
rateio.id_concurso  ->  concurso.id_concurso
rateio.id_faixa  ->  faixa.id_faixa
```

## Detalhe das tabelas

### `tipo_jogo`  (4 linhas)

Catalogo das modalidades (Mega-Sena, Quina, etc) e suas regras (quantas dezenas sao sorteadas, apostadas e disponiveis).

| Coluna | Tipo | Nulo? |
|---|---|---|
| `id_tipo_jogo` | smallint | nao |
| `codigo` | character varying(30) | nao |
| `nome_exibicao` | character varying(60) | nao |
| `dezenas_sorteadas` | smallint | nao |
| `dezenas_apostadas` | smallint | nao |
| `dezenas_disponiveis` | smallint | nao |

**Amostra:**

```json
[
  {
    "id_tipo_jogo": 1,
    "codigo": "MEGA_SENA",
    "nome_exibicao": "Mega-Sena",
    "dezenas_sorteadas": 6,
    "dezenas_apostadas": 6,
    "dezenas_disponiveis": 60
  },
  {
    "id_tipo_jogo": 2,
    "codigo": "QUINA",
    "nome_exibicao": "Quina",
    "dezenas_sorteadas": 5,
    "dezenas_apostadas": 5,
    "dezenas_disponiveis": 80
  }
]
```

### `faixa`  (19 linhas)

Faixas de premiacao de cada modalidade (ex: 6 acertos, 5 acertos...). O campo 'acertos' e o numero de acertos daquela faixa.

| Coluna | Tipo | Nulo? |
|---|---|---|
| `id_faixa` | integer | nao |
| `id_tipo_jogo` | smallint | nao |
| `numero_faixa` | smallint | nao |
| `descricao` | character varying(120) | sim |
| `acertos` | smallint | nao |

**Amostra:**

```json
[
  {
    "id_faixa": 1,
    "id_tipo_jogo": 4,
    "numero_faixa": 1,
    "descricao": "20 acertos",
    "acertos": 20
  },
  {
    "id_faixa": 2,
    "id_tipo_jogo": 4,
    "numero_faixa": 2,
    "descricao": "19 acertos",
    "acertos": 19
  }
]
```

### `localidade`  (5,590 linhas)

Municipio + UF. UF '--' = canal eletronico (compra online, sem cidade fisica).

| Coluna | Tipo | Nulo? |
|---|---|---|
| `id_localidade` | integer | nao |
| `municipio` | character varying(120) | nao |
| `uf` | character(2) | nao |

**Amostra:**

```json
[
  {
    "id_localidade": 1,
    "municipio": "ERECHIM",
    "uf": "RS"
  },
  {
    "id_localidade": 2,
    "municipio": "PONTA GROSSA",
    "uf": "PR"
  }
]
```

### `local_sorteio`  (694 linhas)

Local fisico onde o sorteio ocorreu (ex: CAMINHAO DA SORTE, ESTUDIO DE TV).

| Coluna | Tipo | Nulo? |
|---|---|---|
| `id_local_sorteio` | integer | nao |
| `nome` | character varying(120) | nao |
| `id_localidade` | integer | nao |

**Amostra:**

```json
[
  {
    "id_local_sorteio": 1,
    "nome": "CAMINHÃO DA SORTE",
    "id_localidade": 1
  },
  {
    "id_local_sorteio": 2,
    "nome": "CAMINHÃO DA SORTE",
    "id_localidade": 2
  }
]
```

### `concurso`  (16,958 linhas)

Um concurso de uma modalidade. Tabela central — quase tudo se liga a ela. Traz data de apuracao, se acumulou, valores arrecadados/estimados, etc.

| Coluna | Tipo | Nulo? |
|---|---|---|
| `id_concurso` | integer | nao |
| `id_tipo_jogo` | smallint | nao |
| `numero_concurso` | integer | nao |
| `data_apuracao` | date | nao |
| `data_proximo_concurso` | date | sim |
| `numero_concurso_anterior` | integer | sim |
| `numero_concurso_proximo` | integer | sim |
| `numero_concurso_final_0_5` | integer | sim |
| `id_local_sorteio` | integer | sim |
| `acumulado` | boolean | nao |
| `ultimo_concurso` | boolean | nao |
| `indicador_concurso_especial` | smallint | sim |
| `tipo_publicacao` | smallint | sim |
| `numero_jogo` | smallint | sim |
| `observacao` | text | sim |
| `valor_arrecadado` | numeric | sim |
| `valor_estimado_proximo_concurso` | numeric | sim |
| `valor_acumulado_proximo_concurso` | numeric | sim |
| `valor_acumulado_concurso_especial` | numeric | sim |
| `valor_acumulado_concurso_0_5` | numeric | sim |
| `valor_saldo_reserva_garantidora` | numeric | sim |
| `valor_total_premio_faixa_um` | numeric | sim |
| `created_at` | timestamp with time zone | nao |

**Amostra:**

```json
[
  {
    "id_concurso": 7001,
    "id_tipo_jogo": 2,
    "numero_concurso": 1501,
    "data_apuracao": "2005-09-20",
    "data_proximo_concurso": "2005-09-22",
    "numero_concurso_anterior": 1500,
    "numero_concurso_proximo": 1502,
    "numero_concurso_final_0_5": 1505,
    "id_local_sorteio": 35,
    "acumulado": false,
    "ultimo_concurso": true,
    "indicador_concurso_especial": 1,
    "tipo_publicacao": 3,
    "numero_jogo": 3,
    "observacao": "Estimativa de prêmio (QUINA) para o próximo concurso, a ser realizado em 22/09/2005: R$300.000,00.",
    "valor_arrecadado": null,
    "valor_estimado_proximo_concurso": 0.0,
    "valor_acumulado_proximo_concurso": 0.0,
    "valor_acumulado_concurso_especial": 0.0,
    "valor_acumulado_concurso_0_5": 0.0,
    "valor_saldo_reserva_garantidora": 0.0,
    "valor_total_premio_faixa_um": 0.0,
    "created_at": "2026-09-12T23:28:42.120251+00:00"
  },
  {
    "id_concurso": 7002,
    "id_tipo_jogo": 2,
    "numero_concurso": 1502,
    "data_apuracao": "2005-09-22",
    "data_proximo_concurso": "2005-09-24",
    "numero_concurso_anterior": 1501,
    "numero_concurso_proximo": 1503,
    "numero_concurso_final_0_5": 1505,
    "id_local_sorteio": 35,
    "acumulado": true,
    "ultimo_concurso": true,
    "indicador_concurso_especial": 1,
    "tipo_publicacao": 3,
    "numero_jogo": 3,
    "observacao": "Acumulou!! Estimativa de prêmio (QUINA) para o próximo concurso, a ser realizado em 24/09/2005: R$700.000,00.",
    "valor_arrecadado": null,
    "valor_estimado_proximo_concurso": 0.0,
    "valor_acumulado_proximo_concurso": 307887.3,
    "valor_acumulado_concurso_especial": 0.0,
    "valor_acumulado_concurso_0_5": 0.0,
    "valor_saldo_reserva_garantidora": 0.0,
    "valor_total_premio_faixa_um": 0.0,
    "created_at": "2026-09-12T23:28:42.120251+00:00"
  }
]
```

### `dezena`  (170,442 linhas)

Dezenas sorteadas. 1 LINHA POR DEZENA de cada concurso (nao por concurso). Para jogos com 2 sorteios, 'segundo_sorteio'=true marca as do 2o.

| Coluna | Tipo | Nulo? |
|---|---|---|
| `id_dezena` | integer | nao |
| `id_concurso` | integer | nao |
| `numero` | smallint | nao |
| `ordem_sorteio` | smallint | sim |
| `ordem_crescente` | smallint | sim |
| `segundo_sorteio` | boolean | nao |

**Amostra:**

```json
[
  {
    "id_dezena": 1,
    "id_concurso": 1,
    "numero": 0,
    "ordem_sorteio": 6,
    "ordem_crescente": 1,
    "segundo_sorteio": false
  },
  {
    "id_dezena": 2,
    "id_concurso": 1,
    "numero": 6,
    "ordem_sorteio": 15,
    "ordem_crescente": 2,
    "segundo_sorteio": false
  }
]
```

### `rateio`  (72,171 linhas)

Resultado financeiro por faixa de premiacao de cada concurso: quantos ganhadores e quanto cada um levou.

| Coluna | Tipo | Nulo? |
|---|---|---|
| `id_rateio` | integer | nao |
| `id_concurso` | integer | nao |
| `id_faixa` | integer | nao |
| `numero_ganhadores` | integer | sim |
| `valor_premio` | numeric | sim |
| `valor_total` | numeric | sim |

**Amostra:**

```json
[
  {
    "id_rateio": 27767,
    "id_concurso": 6454,
    "id_faixa": 14,
    "numero_ganhadores": 234,
    "valor_premio": 2063.73,
    "valor_total": 482912.82
  },
  {
    "id_rateio": 27768,
    "id_concurso": 6454,
    "id_faixa": 15,
    "numero_ganhadores": 9154,
    "valor_premio": 25.0,
    "valor_total": 228850.0
  }
]
```

### `ganhador_municipio`  (16,227 linhas)

De quais municipios sairam os ganhadores de cada concurso (fonte: API de concursos).

| Coluna | Tipo | Nulo? |
|---|---|---|
| `id_ganhador_municipio` | integer | nao |
| `id_concurso` | integer | nao |
| `id_localidade` | integer | nao |
| `numero_ganhadores` | integer | sim |
| `posicao` | smallint | sim |
| `nome_fantasia_ul` | character varying(120) | sim |
| `serie` | character varying(30) | sim |

**Amostra:**

```json
[
  {
    "id_ganhador_municipio": 1,
    "id_concurso": 16,
    "id_localidade": 5,
    "numero_ganhadores": 2,
    "posicao": 1,
    "nome_fantasia_ul": null,
    "serie": ""
  },
  {
    "id_ganhador_municipio": 2,
    "id_concurso": 17,
    "id_localidade": 5,
    "numero_ganhadores": 1,
    "posicao": 1,
    "nome_fantasia_ul": null,
    "serie": ""
  }
]
```

### `loterica`  (48,957 linhas)

Loterica fisica ou canal de venda (nome fantasia, canal de vendas).

| Coluna | Tipo | Nulo? |
|---|---|---|
| `id_loterica` | integer | nao |
| `razao_social` | character varying(120) | nao |
| `nome_fantasia` | character varying(120) | sim |
| `id_localidade` | integer | sim |
| `canal_vendas` | character varying(20) | nao |

**Amostra:**

```json
[
  {
    "id_loterica": 1,
    "razao_social": "LOTERIAS ZEBRAS II",
    "nome_fantasia": "LOTERIAS ZEBRAS II",
    "id_localidade": 347,
    "canal_vendas": "Físico"
  },
  {
    "id_loterica": 2,
    "razao_social": "PORTA DA ESPERANCA",
    "nome_fantasia": "PORTA DA ESPERANCA",
    "id_localidade": 416,
    "canal_vendas": "Físico"
  }
]
```

### `ganhador_loterica`  (3,054,637 linhas)

MAIOR TABELA. Ganhadores rastreados a nivel de loterica especifica (fonte: API 'locais da sorte'). Liga concurso + loterica + faixa.

| Coluna | Tipo | Nulo? |
|---|---|---|
| `id_ganhador_loterica` | integer | nao |
| `id_concurso` | integer | nao |
| `id_loterica` | integer | nao |
| `id_faixa` | integer | nao |
| `canal_vendas` | character varying(30) | sim |
| `tipo_aposta` | character varying(30) | sim |
| `numero_cotas` | smallint | sim |
| `quantidade_numeros_apostados` | smallint | sim |
| `quantidade_premios_por_faixa` | integer | sim |
| `premio_total` | numeric | sim |
| `teimosinha` | boolean | sim |

**Amostra:**

```json
[
  {
    "id_ganhador_loterica": 1,
    "id_concurso": 8028,
    "id_loterica": 1,
    "id_faixa": 13,
    "canal_vendas": "Físico",
    "tipo_aposta": "Simples",
    "numero_cotas": 1,
    "quantidade_numeros_apostados": 15,
    "quantidade_premios_por_faixa": 1,
    "premio_total": 1245054.98,
    "teimosinha": false
  },
  {
    "id_ganhador_loterica": 2,
    "id_concurso": 8028,
    "id_loterica": 2,
    "id_faixa": 14,
    "canal_vendas": "Físico",
    "tipo_aposta": "Simples",
    "numero_cotas": 1,
    "quantidade_numeros_apostados": 15,
    "quantidade_premios_por_faixa": 1,
    "premio_total": 1418.03,
    "teimosinha": true
  }
]
```

## Notas importantes pra analise

- **`dezena` e por dezena**, nao por concurso: pra ter as dezenas de um concurso, agregue (ex: `array_agg(numero ORDER BY numero)`).
- **`rateio` e por faixa**: cada concurso tem N linhas (uma por faixa de premiacao).
- **`ganhador_loterica`** e a maior tabela (~2.8M linhas) — cuidado com full scans; filtre por `id_concurso`.
- **Canal eletronico** (compra online) aparece como `localidade.uf = '--'` e `municipio = 'CANAL ELETRONICO'`.
- **`local_sorteio.nome`** e normalizado (UPPER, sem acento, variantes unificadas).
- Alguns mega-acumulados gigantes tem **coleta parcial** de `ganhador_loterica` (limite de 100k registros da API da Caixa) — ver `pipeline.v_fila_detalhe` status='parcial'.

## Views de saude/exploracao (schema `pipeline`)

Ja existem views prontas: `v_saude_cobertura`, `v_saude_filas`, `v_saude_problemas`, `v_saude_lacunas`, `v_saude_volumes`, `v_saude_execucoes`, `v_concurso_detalhe`, `v_fila_detalhe`. Veja `loterias/sql/views_saude.sql`.