# Notas sobre os dados da fonte

Peculiaridades das APIs da Caixa que o pipeline precisa tratar. Cada item aqui
custou investigação; deixo registrado para não redescobrir depois.

---

## 1. Município vazio nos registros históricos

A API de concursos devolve `listaMunicipioUFGanhadores` com `municipio` vazio
em parte dos registros. **Não indica canal digital** — é limitação histórica de
registro da própria Caixa.

Na Mega-Sena o corte é limpo:

| Faixa de concursos | Ganhadores sem município | Leitura |
|---|---:|---|
| 1 – 1.070 | 263 de 263 (100%) | cidade não era registrada nessa época |
| 1.071 – 1.076 | — | nenhum ganhador de prêmio principal |
| 1.077 em diante | 0% | registro completo |

Nas outras modalidades a transição é embaralhada: o primeiro ganhador com
município aparece antes do último sem município (Quina 2.062 e 5.002,
Lotomania 767 e 931), e a Lotofácil ainda tem ganhadores sem município em
concursos recentes.

Normalização: município vazio vira `NAO INFORMADO` no silver. A UF é
preservada quando presente.

---

## 2. Capitalização de município mudou ao longo do tempo

Concursos antigos vêm em Title Case (`"Anápolis"`), recentes em caixa alta
(`"ANÁPOLIS"`). Somando caixa, acento e espaços, os ganhadores das quatro
modalidades trazem **2.405 pares município/UF crus que colapsam em 2.062** —
sem normalização seriam 343 duplicatas na tabela `localidade`.

Tratamento em `parse_municipio_uf()`, em `loterias/silver/concursos.py`:

- `.upper()` no nome do município;
- remoção de acentuação via `unicodedata` — municípios são gravados **sem
  acento**, para não duplicar por inconsistência de encoding da API
  (`BRASILIA` vs `BRASÍLIA`);
- `re.sub(r"\s+", " ", ...)` para colapsar espaços internos;
- `.upper()[:2]` na UF.

---

## 3. O local do sorteio vinha em dezenas de variações

Caixa, acento, pontuação e erros de digitação faziam o mesmo lugar aparecer
com nomes diferentes. Nas quatro modalidades são **58 variações para 11
lugares**:

| Nome canônico | Variantes absorvidas (exemplos) |
|---|---|
| `CAMINHÃO DA SORTE` | `Caminhão da sorte`, `CamInhão da Sorte`, `CAINHÃO DA SORTE`, `Caminhão da Sorte09`, `Caminhão de Sorte`, `CAMINHÃO DA CAIXA`, `Caminhao do` |
| `ESPAÇO LOTERIAS CAIXA` | `Espaço Caixa Loterias`, `ESPAÇO LOOTERIAS CAIXA`, `ESAPÇO LOTERIAS CAIXA`, `ESPÇAO LOTERIAS CAIXA`, `ESPAÇO LOTERIAS CAICA`, `Espaço Caixa Loterais`, `ÉSPAÇO LOTERIAS CAIXA` |
| `ESPAÇO DA SORTE` | `Espaço da Sorte` |
| `ESTÚDIO DE TV` | `Estudio de TV`, `ESTÚDIO DE TV.`, `Estúdio de tv.`, `Estúdio TV` |
| `ESTÚDIO DE TV REDE GLOBO` | `Estúdio de TV Globo`, `ESTÚDIO REDE GLOBO`, `ESTUDIO DE TV REDE GLOBO/SP` |
| `AUDITÓRIO` | `Auditorio` |
| `AUDITÓRIO DA CAIXA` | local específico, mantido separado |
| `AUDITÓRIO 512 NORTE` | local específico em Brasília |
| `PALCO PRINCIPAL - SÃO JOÃO`, `PARQUE DO POVO` | sorteios especiais em Campina Grande |
| `CENTRO DE TRADIÇÕES NORDESTINAS` | sorteio especial em São Paulo |

O catálogo mora em `config/loterias.yaml`, na seção
`normalizacao.local_sorteio`. A comparação usa uma chave sem acento, caixa e
pontuação, então só erro de digitação de verdade precisa ser listado. Nome
fora do catálogo cai no `.upper()` e aparece no `loterias-admin saude`.

Os casos duvidosos foram decididos pelo contexto: `CAMINHÃO DA CAIXA`,
`Caminhão` e `Caminhao do` aparecem na mesma cidade em que o concurso seguinte
foi sorteado no Caminhão da Sorte. Um concurso da Quina traz `QUINA` no campo
— o nome da modalidade — e fica sem local, porque não há como saber o lugar
sem chutar.

---

## 4. Campos que a API devolve mas não preenche

| Campo | Situação |
|---|---|
| `id` | nulo em 100% dos concursos |
| `listaDezenasSegundoSorteio` | nulo na Mega-Sena (só Dupla Sena usa) |
| `listaResultadoEquipeEsportiva` | nulo — campo de outras modalidades |
| `premiacaoContingencia` | nulo |
| `valorSaldoReservaGarantidora` | sempre zero |
| `valorTotalPremioFaixaUm` | sempre zero |
| `ultimoConcurso` | sempre `True`, sem variação — não serve para análise |
| `nomeTimeCoracaoMesSorte` | 815 registros com bytes nulos `\x00` |

---

## 5. UF truncada em 1 caractere

Erro de digitação no cadastro original da Caixa, confirmado consultando a URL
pública da API. Está detalhado como bug 1 no README; aqui fica o resumo da
fonte:

| Modalidade | Concurso | Município | UF na API | UF correta |
|---|---:|---|---|---|
| LOTOFACIL | 2030 | SANTA HELENA DE GOIAS | `G` | `GO` |
| LOTOFACIL | 2034 | FORTALEZA | `C` | `CE` |

O dado vai continuar errado na fonte para sempre. O tratamento é no silver.

---

## 6. Canal eletrônico não é cidade

Compra pela internet aparece no campo de município. A Caixa adotou
`municipio = 'CANAL ELETRONICO'` e `uf = '--'` como padrão desde outubro de
2019, mas antes disso usou variantes.

As variantes conhecidas estão em `config/loterias.yaml`, na seção
`normalizacao.canal_eletronico`:

```
CANAL ELETRONICO, CANAL DIGITAL, CANAIS ELETRONICOS,
INTERNET BANKING, LOTERIAS EM CANAIS ELETRONICOS
```

Quando descobrir uma variante nova, basta acrescentar na lista — o silver lê
do YAML.

---

## 7. A API de locais da sorte

Endpoint: `servicebus3.caixa.gov.br/portaldeloterias/api/locais-da-sorte`

Parâmetros:

| Parâmetro | Observação |
|---|---|
| `modalidade` | `MEGA_SENA`, `LOTOFACIL`, ... |
| `concurso` | número do concurso |
| `pageSize` | máximo testado: 1000 |
| `pagina` | começa em 1 |
| `_` | cache-buster: timestamp Unix em milissegundos |

Diferenças em relação à API de concursos:

- **Separador de cidade/UF é barra** (`FEIRA DE SANTANA/BA`), não vírgula.
- **`razaoSocial` só existe a partir de 21/11/2023.** Antes disso vem vazio
  em todas as modalidades; depois, vem preenchido em parte dos registros — hoje
  46% dos 4,5 milhões coletados. O identificador estável da lotérica é
  `unidadeLoterica` (nome fantasia). O silver usa a razão social quando ela
  vem e cai no nome fantasia normalizado quando está vazia, para satisfazer o
  `NOT NULL` do gold.
- **Cidade `"/"`** aparece em registros de internet banking. Vira
  `('NAO INFORMADO', 'NA')`.
- **Canal digital**: `canalVendas = "Digital"` vem com
  `unidadeLoterica = "LOTERIAS EM CANAIS ELETRONICOS"`. Quando a cidade está
  preenchida, é a cidade do apostador, não de uma loja física.

Campos com tipo disfarçado de string, que o silver converte:

| Campo | Tipo bruto | Vira |
|---|---|---|
| `premioTotal` | `"R$X.XXX,XX"` | float |
| `quantidadeNumerosApostados` | string | int |
| `teimosinha` | `"Sim"` / `"Não"` | bool |
| `faixaAcertos` | `"N acertos"` | int |

---

## 8. Data do próximo sorteio

`dataProximoConcurso` é a data **prevista** do sorteio seguinte, publicada
junto com o resultado. Tem três comportamentos na fonte:

| Situação | Onde | O que o pipeline faz |
|---|---|---|
| Campo vazio | Mega-Sena 1–281, Quina 1–875, Lotomania 1–134 | preenche com a apuração do concurso seguinte |
| Data do sorteio **anterior**, deslocada uma posição | Lotomania 178–190, Mega-Sena 325–337, Quina 892–2153 (92 concursos) | substitui pela apuração do concurso seguinte |
| Prevista diferente da real | 49 concursos | mantém: o sorteio foi adiado, e a previsão é informação |

Dois sorteios no mesmo dia também acontecem (13 casos): aí a data do próximo
é igual à do próprio concurso, e está certa.

A regra mora em `loterias/gold/data_proximo.py` e roda depois de cada carga do
gold. Detalhes do bug que isso corrigiu estão no README, bug 7.

---

## 9. Município escrito errado

O município vem digitado à mão em três campos — cidade do sorteio, cidade do
ganhador e cidade da lotérica — e 97 das 5.667 localidades não batiam com o
cadastro do IBGE:

| Tipo | Exemplos |
|---|---|
| Erro de digitação | `SAO PULO`, `SA0 PAULO`, `URBERLANDIA`, `URBELANDIA`, `OSACO`, `TMON`, `CONT AGEM`, `VILHA VELHA` |
| Grafia alternativa | `SANTA BARBARA D OESTE`, `DOESTE`, `DO OESTE`; `MOGI-GUACU`; `STO. ANTONIO DA PLATINA`; `SANT' ANA DO LIVRAMENTO` |
| Pontuação ou UF colada | `PONTA GROSSA.`, `RIBEIRAO PRETO,`, `IMBITUVA, PR` |
| Nome antigo | `EMBU` (hoje `EMBU DAS ARTES`), `PARATI`, `AUGUSTO SEVERO` (hoje `CAMPO GRANDE/RN`), `ACU` (hoje `ASSU`) |
| Distrito, não município | `VICENTE DE CARVALHO` (Guarujá), `MUQUEM` (Niquelândia), `GAMA` e `TAGUATINGA` (Brasília) |
| UF trocada | `BRASILIA/SP`, `CERQUILHO/ES` |
| Espaço duplo | `CAMPO  GRANDE/MS`, `PORTO  VELHO/RO`, `TRES  LAGOAS/MS` |

A correção fica em `loterias/silver/ibge.py` (`corrigir_municipio`) e só age
com evidência: lista revisada no YAML (`normalizacao.municipio.correcoes`),
regras de escrita cujo resultado existe no IBGE, ou semelhança acima de 0,8
com folga de 0,05 sobre o segundo candidato da mesma UF.

Ficou de fora `VARZEA GRANDE/CE`, que não existe no Ceará e pode ser a do
Piauí ou a do Mato Grosso.

---

## 10. Moeda e arrecadação

**Cruzeiro Real.** A Quina começou em 13/03/1994, antes do Plano Real. Os
concursos 1 a 28 (até 26/06/1994) trazem todos os valores em CR$: prêmio,
estimativa e acumulado. O silver divide por 2.750, a paridade fixada na
entrada do Real em 01/07/1994. Nenhuma outra modalidade tem concurso anterior
a essa data.

**Arrecadação.** `valorArrecadado` só vem preenchido a partir de meados de
2009; antes disso a API devolve `0`. Como concurso nenhum arrecada zero, o
silver trata como ausente.

| Modalidade | Primeira arrecadação informada |
|---|---|
| QUINA | 1996, esporádica; contínua a partir de 2009 |
| MEGA_SENA | 1998, esporádica; contínua a partir de 2009 |
| LOTOMANIA | 2007, esporádica; contínua a partir de 2009 |
| LOTOFACIL | 2009 |

Consequência para análise: retorno de prêmios sobre arrecadação só faz
sentido comparando os dois no mesmo conjunto de concursos.

**Local do sorteio vazio.** Quando a API manda a cidade mas não o nome do
local, o silver registra o local `NAO INFORMADO` naquela cidade, para a cidade
não se perder (393 concursos).

---

## 11. A numeração das faixas muda com o tempo

O número da faixa (`faixa` em `listaRateioPremio`) não é uma identidade
estável. Na Lotomania, a partir do concurso 1653, a Caixa criou a faixa de
15 acertos com o número 6 e passou o "0 acertos", que era a 6, para a 7.

A identidade real de uma faixa é o par número + descrição de acertos. O
pipeline usa esse par em todas as camadas; uma verificação que pega a
confusão é conferir que nenhum concurso tem duas faixas com o mesmo número
de acertos. Isso vale para as quatro modalidades ativas — a Dupla Sena, que
tem dois sorteios por concurso, vai exigir outra chave quando for ativada.
