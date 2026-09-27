# Decisões de engenharia

Este documento guarda o "porquê" das escolhas do pipeline. O código diz o que
acontece; aqui está o motivo.

---

## Por que as duas fontes coletam uma modalidade por vez

As duas APIs da Caixa limitam requisições, e a DAG encadeia as modalidades nos
dois TaskGroups: uma de cada vez, nunca quatro em paralelo.

- **`servicebus3`** (locais da sorte) **bane IP** quando detecta concorrência.
  Responde 403 e o bloqueio dura horas. Entre requisições vai
  `request_sleep: 4.0` — cerca de 15 req/min, ritmo confirmado seguro em campo.
- **`servicebus2`** (concursos) é mais tolerante, mas também limita. Cada task
  já usa semáforo de 3 requisições; com quatro modalidades em paralelo isso
  vira 12 simultâneas, e aí vem 429.

O encadeamento dos concursos veio de evidência, não de precaução. Numa execução
em 13/09/2026 as quatro modalidades dispararam juntas e **cada uma coletou
exatamente 20 concursos e levou HTTP 429 nos quatro seguintes** — o padrão de
um limite por janela de tempo, não de concurso inexistente. Confirmei consultando os concursos
seguintes um a um: respondiam 200 normalmente.

Com as tasks encadeadas, a pressão cai de 12 requisições concorrentes para 3, e
o laço incremental consegue recuperar o atraso inteiro numa passada em vez de
parar nas primeiras falhas.

Concursos vêm antes de locais porque o bronze de locais lê `public.concurso`
para montar a própria fila.

---

## Por que o backoff é em escala de dias

O scheduler roda **uma vez por dia**. Um backoff em minutos faria o run ficar
preso esperando uma API que só volta no dia seguinte.

```yaml
fila:
  max_tentativas: 10
  backoff_horas_por_tentativa: [6, 12, 24, 48, 48, 72, 72, 96, 96, 168]
```

Primeira falha → tenta de novo em 6h. Terceira → 24h. Da quarta em diante,
48h ou mais. O item vira `abandonado` na décima falha, então contam as nove
primeiras esperas: 474 horas, quase **20 dias**. Como o run é diário, cada
espera termina no run seguinte, e na prática passa de três semanas. O último
degrau, 168h, nunca chega a ser usado.

A consequência prática é a filosofia de **run curto e defensivo**:

| Antes | Agora |
|---|---|
| 15 retries inline × 480s = ~2h com o run preso | 3 retries inline × 60s, depois vai pra fila |
| Backoff `5 × 2^n` minutos, ~2,5h até abandonar | Backoff em dias, ~20 dias até abandonar |
| HTTP 404 = `sem_dados` permanente | HTTP 404 = `erro` (pode ser "ainda não publicado"), retenta |
| 1 concurso falho trava o pipeline inteiro | o pipeline segue e o item volta amanhã |

Um run típico dura **minutos**. Se a API estiver banindo, o run termina rápido
e tenta no dia seguinte.

`max_retries_inline` cobre apenas 429 momentâneos (janela de ~30s), nunca bans
longos.

---

## Por que Timemania e Dupla Sena estão fora

As duas têm estrutura de JSON diferente do padrão das outras quatro e exigem
tratamento próprio que ainda não implementei:

- **Timemania** tem o campo `nomeTimeCoracaoMesSorte` (time do coração), que
  chega com bytes nulos `\x00` em cerca de 800 registros.
- **Dupla Sena** tem **dois sorteios por concurso** — `listaDezenas` e
  `listaDezenasSegundoSorteio` — e faixas de premiação repetindo o mesmo número
  de acertos (6, 5, 4, 3 em cada sorteio).

Para reativar: acrescentar o bloco da modalidade em `config/loterias.yaml`,
rodar `loterias-admin db recriar --confirmar` e ajustar
`loterias/silver/concursos.py` para modelar o segundo sorteio.

O esquema do banco já está preparado para a Dupla Sena: a coluna
`dezena.segundo_sorteio` existe e a constraint que impedia faixas com o mesmo
número de acertos foi removida (ver bug 3 no README).

---

## Por que o primeiro concurso com locais da sorte varia por modalidade

A Caixa só começou a publicar locais da sorte no `servicebus3` a partir de
setembro de 2020. O primeiro concurso com dados é diferente em cada
modalidade:

| Modalidade | `concurso_min` | Data do sorteio |
|---|---:|---|
| MEGA_SENA | 2296 | 2020-09-05 |
| QUINA | 5358 | 2020-09-04 |
| LOTOFACIL | 2028 | 2020-09-03 |
| LOTOMANIA | 2106 | 2020-09-04 |

Concursos abaixo desses pisos devolvem lista vazia. Configurar o piso em
`locais_sorte.concurso_min` evita quase **12.000 chamadas inúteis** ao
endpoint mais sensível da Caixa.

---

## Por que existem arquivos `PARCIAL` no bronze

O endpoint de locais da sorte tem um teto de paginação. Em concursos com
acumulado muito grande, a API para de devolver páginas antes de entregar o
total que ela mesma declara.

Quando isso acontece, o scraper salva o que conseguiu num arquivo com o
sufixo `PARCIAL` e marca o item da fila como **terminal** — não retenta, porque
insistir não traz mais dados.

```
megasena_locais_sorte_2550_2550_PARCIAL_20260608.parquet
```

Esses arquivos **não são descartáveis**: para os concursos 2550, 2810 e 2955
da Mega-Sena eles são a única cópia dos dados. O arquivo de faixa larga que
cobre o mesmo intervalo tem zero linhas para esses concursos.

`loterias/admin/registro_bronze.py` reconhece o sufixo, e
`tests/test_registro_bronze.py` trava esse comportamento.

---

## Por que o dado bruto vai versionado no repositório

`data/bronze/` tem 647 parquets e 137 MB. É o resultado de uma coleta
histórica que levou cerca de **16 horas** por causa do limite de requisições da
Caixa.

Mantenho no Git porque:

- um `git clone` num computador novo já traz tudo, sem re-raspar os primeiros
  milhares de concursos de cada modalidade;
- quem quiser o dataset baixa junto com o código, sem passo extra;
- o projeto roda de ponta a ponta **sem encostar na API da Caixa**.

Não é um repositório que recebe commit de dado todo dia: é um backfill
histórico, commitado uma vez. As atualizações incrementais do dia a dia ficam
locais.

Cabe com folga — o limite do GitHub é 100 MB por arquivo e o maior parquet
aqui tem 2,8 MB.

O que **não** vai pro Git:

- `data/silver/` (104 MB) — reproduzível a partir do bronze em cerca de 10
  minutos;
- `data/_cache/` — o cadastro de municípios do IBGE, que rebaixa em segundos.

---

## Por que não existe painel web próprio

Um HTML aberto via `file://` não consegue executar Python — o sandbox do
navegador impede iniciar processo. Um painel próprio exigiria escrever um
servidor, e aí seria uma aplicação web inteira pendurada num projeto de dados:
não demonstra nada de engenharia de dados e vira superfície de manutenção.

A divisão que uso:

- **Airflow** cobre as ações. As DAGs `loterias_admin_*` usam `params`, então o
  formulário "Trigger DAG w/ config" sai de graça, com validação, log de cada
  execução, histórico de quem rodou o quê e quando, retry e alerta.
- **Metabase** cobre a visão, lendo as views `pipeline.v_saude_*`.

Como a lógica mora em funções importáveis dentro de `loterias/admin/`, qualquer
interface futura é um wrapper de poucas linhas. Se um dia eu quiser uma tela
única, um Streamlit sai em cerca de 150 linhas.

---

## Por que a lógica de administração não mora no comando do CLI

Cada operação em `loterias/admin/` é uma função com assinatura própria que
**devolve um resultado**, em vez de imprimir e sair:

```python
def resetar(de, fonte, modalidade=None, todas=False, dias=None,
            zerar_tentativas=True, dry_run=False) -> ResultadoReset:
```

O Typer em `loterias/admin/cli.py` é só uma casca fina por cima. O motivo é
que, com a lógica importável, a DAG do Airflow chama a função, o teste chama a
função, e uma interface futura chama a função. Se a lógica ficasse dentro do
comando do Typer, toda interface nova viraria `subprocess` parseando stdout,
que é frágil.

Regras de desenho do CLI:

- **`--dry-run` em todo comando que escreve**, mostrando a contagem do que
  seria afetado antes de aplicar.
- **`--confirmar` obrigatório no destrutivo** (`db recriar`, `db limpar`).
- **Nenhuma modalidade com valor padrão silencioso**: ou `--modalidade`
  explícita, ou `--todas`.
- **`saude` devolve código de saída diferente de zero quando degradado**, então
  serve como task no Airflow e passo no CI.

---

## Por que os dois perfis de conexão

`LOTERIAS_DB_PERFIL` alterna entre:

- **`local`** — o Postgres do `docker-compose`, sem SSL (`sslmode=disable`).
  É o padrão e o caminho que o README apresenta.
- **`gerenciado`** — um Postgres de nuvem, com `sslmode=require` e certificado
  em `LOTERIAS_DB_CA`.

Trocar de ambiente é editar o `.env.local`, nunca o código.

Todas as variáveis levam o prefixo `LOTERIAS_DB_` de propósito. A versão
anterior lia `os.environ["USER"]` direto — e `USER` já existe no ambiente de
qualquer Linux ou container. Em vez de falhar avisando que faltava
configuração, o código conectaria com o usuário errado em silêncio.

---

## Por que o scraper se identifica em vez de imitar navegador

O `User-Agent` é um campo de texto livre que o cliente manda junto da
requisição. Ninguém valida — dá para escrever qualquer coisa. A escolha, então,
é sobre postura.

Os dois scrapers mandam o mesmo:

```
Mozilla/5.0 (compatible; projeto_airflow_loterias/0.1.0;
             +https://github.com/DenisMilanez/projeto_airflow_loterias)
```

É a convenção que o Googlebot usa: nome, versão e um link onde quem opera o
servidor descobre o que está batendo nele. Se a Caixa estranhar o tráfego, abre
o repositório e vê um coletor de dado público que respeita ritmo e faz backoff
— em vez de precisar decidir sobre um Chrome suspeito.

A versão sai de `importlib.metadata`, então acompanha o `pyproject.toml`
sozinha.

Antes o scraper de locais da sorte mandava um `User-Agent` de Chrome, por
suposição de que o `servicebus3` filtrava por isso. Testei os três cenários
contra a API: Chrome com `Referer`/`Origin`, agente honesto com eles, e agente
honesto sem eles. Os três responderam HTTP 200. **A API não filtra por
`User-Agent`** — os bloqueios que aconteceram foram por taxa, que o
`request_sleep` e a escada de backoff já tratam.

O `Referer` e o `Origin` continuam no `servicebus3`. Eles declaram qual
superfície da API está sendo usada, não quem é o cliente — não é a parte que
disfarça.

Os cabeçalhos moram em `loterias/http.py`, num lugar só. Antes cada scraper
tinha a própria cópia, e foi exatamente por isso que os dois divergiram.
