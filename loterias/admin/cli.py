from __future__ import annotations

from enum import StrEnum
from typing import Annotated

import typer

from loterias.admin import arquivo as mod_arquivo
from loterias.admin import banco as mod_banco
from loterias.admin import catalogo as mod_catalogo
from loterias.admin import dados as mod_dados
from loterias.admin import fila as mod_fila
from loterias.admin import saude as mod_saude

app = typer.Typer(help="Administracao do pipeline de loterias.", no_args_is_help=True)
app_fila = typer.Typer(help="Filas de coleta por concurso.", no_args_is_help=True)
app_arquivo = typer.Typer(
    help="Estado dos arquivos nas camadas prata e ouro.", no_args_is_help=True
)
app_db = typer.Typer(help="Schema, conexao e views do banco.", no_args_is_help=True)
app_dados = typer.Typer(help="Backup e catalogo dos dados.", no_args_is_help=True)

app.add_typer(app_fila, name="fila")
app.add_typer(app_arquivo, name="arquivo")
app.add_typer(app_db, name="db")
app.add_typer(app_dados, name="dados")


class Fonte(StrEnum):
    concursos = "concursos"
    locais_sorte = "locais_sorte"


class StatusOrigem(StrEnum):
    erro = "erro"
    processando = "processando"
    abandonado = "abandonado"


class Camada(StrEnum):
    prata = "prata"
    ouro = "ouro"


OpcaoModalidade = Annotated[
    str | None, typer.Option("--modalidade", help="Modalidade alvo, ex: MEGA_SENA")
]
OpcaoFonte = Annotated[
    Fonte, typer.Option("--fonte", help="Fila de concursos ou de locais da sorte")
]
OpcaoDryRun = Annotated[
    bool, typer.Option("--dry-run", help="Mostra o que seria afetado, sem aplicar")
]

VERDE = typer.colors.GREEN
VERMELHO = typer.colors.RED
AMARELO = typer.colors.YELLOW


def _abortar(mensagem: str) -> None:
    typer.secho(f"[X] {mensagem}", fg=VERMELHO, err=True)
    raise typer.Exit(code=2)


def _titulo(texto: str) -> None:
    typer.echo(f"\n{texto}")
    typer.echo("-" * max(len(texto), 58))


def _quando(momento) -> str:
    return momento.strftime("%Y-%m-%d %H:%M") if momento else "-"


@app_fila.command("status")
def fila_status(
    fonte: OpcaoFonte = Fonte.locais_sorte,
    modalidade: OpcaoModalidade = None,
) -> None:
    linhas = mod_fila.status(fonte=fonte.value, modalidade=modalidade, todas=modalidade is None)
    if not linhas:
        typer.echo("fila vazia para o filtro informado")
        return

    typer.echo(f"{'modalidade':<14} {'status':<14} {'qtd':>8}  ultima tentativa")
    typer.echo("-" * 60)
    for linha in linhas:
        typer.echo(
            f"{linha.modalidade:<14} {linha.status:<14} {linha.quantidade:>8}  "
            f"{_quando(linha.ultima_tentativa)}"
        )

    extensao = mod_fila.faixa(fonte=fonte.value, modalidade=modalidade, todas=modalidade is None)
    if extensao["total"]:
        typer.echo(
            f"\n{extensao['total']} entrada(s), concursos de {extensao['menor']} a {extensao['maior']}"
        )


@app_fila.command("erros")
def fila_erros(
    fonte: OpcaoFonte = Fonte.locais_sorte,
    modalidade: OpcaoModalidade = None,
    limite: Annotated[int, typer.Option("--limite", help="Quantos grupos listar")] = 10,
) -> None:
    grupos = mod_fila.erros(
        fonte=fonte.value, modalidade=modalidade, todas=modalidade is None, limite=limite
    )
    if not grupos:
        typer.secho("nenhum erro ou abandono na fila", fg=VERDE)
        return
    typer.echo(f"{'modalidade':<14} {'qtd':>6}  mensagem")
    typer.echo("-" * 76)
    for grupo in grupos:
        typer.echo(f"{grupo.modalidade:<14} {grupo.quantidade:>6}  {grupo.detalhe[:52]}")


@app_fila.command("detalhe")
def fila_detalhe(
    fonte: OpcaoFonte = Fonte.locais_sorte,
    modalidade: OpcaoModalidade = None,
    status: Annotated[
        list[StatusOrigem] | None,
        typer.Option("--status", help="Repita para incluir mais de um"),
    ] = None,
    limite: Annotated[int, typer.Option("--limite")] = 50,
) -> None:
    escolhidos = tuple(s.value for s in status) if status else ("processando", "erro", "abandonado")
    itens = mod_fila.detalhe(
        fonte=fonte.value,
        modalidade=modalidade,
        todas=modalidade is None,
        status=escolhidos,
        limite=limite,
    )
    if not itens:
        typer.secho(f"nenhum item em {', '.join(escolhidos)}", fg=VERDE)
        return
    typer.echo(
        f"{'modalidade':<13} {'concurso':>9} {'status':<13} {'tent':>5}  {'proximo retry':<17} detalhe"
    )
    typer.echo("-" * 100)
    for item in itens:
        tentativas = f"{item.tentativas}/{item.max_tentativas}"
        typer.echo(
            f"{item.modalidade:<13} {item.numero_concurso:>9} {item.status:<13} "
            f"{tentativas:>5}  {_quando(item.proximo_retry):<17} {item.detalhe[:40]}"
        )


@app_fila.command("resetar")
def fila_resetar(
    de: Annotated[
        list[StatusOrigem],
        typer.Option("--de", help="Status de origem; repita para resetar varios de uma vez"),
    ],
    fonte: OpcaoFonte = Fonte.locais_sorte,
    modalidade: OpcaoModalidade = None,
    todas: Annotated[bool, typer.Option("--todas", help="Aplica a todas as modalidades")] = False,
    dias: Annotated[
        int | None, typer.Option("--dias", help="So o que parou ha mais de N dias")
    ] = None,
    zerar_tentativas: Annotated[
        bool, typer.Option("--zerar-tentativas/--manter-tentativas")
    ] = True,
    dry_run: OpcaoDryRun = False,
) -> None:
    origens = [s.value for s in de]
    try:
        resultado = mod_fila.resetar(
            de=origens,
            fonte=fonte.value,
            modalidade=modalidade,
            todas=todas,
            dias=dias,
            zerar_tentativas=zerar_tentativas,
            dry_run=dry_run,
        )
    except (mod_fila.ModalidadeNaoInformada, mod_fila.FonteInvalida, mod_fila.StatusInvalido) as e:
        _abortar(str(e))
        return

    alvo = ", ".join(resultado.modalidades)
    rotulo = " + ".join(origens)
    if resultado.dry_run:
        typer.echo(
            f"[dry-run] {resultado.afetados} linha(s) seriam resetadas "
            f"de '{rotulo}' em {alvo} ({fonte.value})"
        )
        return

    typer.secho(
        f"{resultado.afetados} linha(s) resetadas para 'pendente' em {alvo} ({fonte.value})",
        fg=VERDE,
    )
    for status in sorted(set(resultado.antes) | set(resultado.depois)):
        antes, depois = resultado.antes.get(status, 0), resultado.depois.get(status, 0)
        if antes != depois:
            typer.echo(f"  {status:<14} {antes:>7} -> {depois:>7}")


@app_arquivo.command("resetar")
def arquivo_resetar(
    camada: Annotated[Camada, typer.Option("--camada", help="Camada a resetar")],
    id_inicio: Annotated[int | None, typer.Option("--id-inicio")] = None,
    id_fim: Annotated[int | None, typer.Option("--id-fim")] = None,
    fonte: Annotated[
        Fonte | None, typer.Option("--fonte", help="Só os arquivos desta fonte")
    ] = None,
    dry_run: OpcaoDryRun = False,
) -> None:
    try:
        resultado = mod_arquivo.resetar(
            camada.value, id_inicio, id_fim, dry_run, fonte.value if fonte else None
        )
    except (mod_arquivo.CamadaInvalida, ValueError) as e:
        _abortar(str(e))
        return
    prefixo = "[dry-run] " if resultado.dry_run else ""
    verbo = "seriam resetados" if resultado.dry_run else "resetados"
    typer.echo(f"{prefixo}{resultado.afetados} arquivo(s) {verbo} na camada {camada.value}")


@app_db.command("testar")
def db_testar() -> None:
    try:
        info = mod_banco.testar()
    except Exception as e:
        _abortar(f"falha na conexao: {e}")
        return
    typer.secho("conexao ok", fg=VERDE)
    typer.echo(f"  {info.descricao}")
    typer.echo(f"  banco:  {info.banco}")
    typer.echo(f"  versao: {info.versao}")


@app_db.command("catalogo")
def db_catalogo(
    bancos: Annotated[
        bool, typer.Option("--bancos", help="Lista tambem os bancos do servidor")
    ] = False,
) -> None:
    if bancos:
        _titulo("Bancos no servidor")
        for nome in mod_banco.bancos():
            typer.echo(f"  {nome}")

    tabelas = mod_banco.catalogo()
    if not tabelas:
        typer.echo("nenhuma tabela em public/pipeline")
        return
    _titulo("Tabelas")
    typer.echo(f"{'schema':<10} {'tabela':<24} {'linhas':>12}")
    typer.echo("-" * 50)
    for tabela in tabelas:
        typer.echo(f"{tabela.schema:<10} {tabela.nome:<24} {tabela.linhas:>12,}")


@app_db.command("recriar")
def db_recriar(
    confirmar: Annotated[
        bool, typer.Option("--confirmar", help="Obrigatorio: a operacao apaga tudo")
    ] = False,
    registrar_bronze: Annotated[bool, typer.Option("--registrar-bronze")] = False,
) -> None:
    try:
        resultado = mod_banco.recriar(confirmar=confirmar, registrar_bronze=registrar_bronze)
    except mod_banco.ConfirmacaoAusente as e:
        _abortar(str(e))
        return
    for modulo in resultado.modulos_ok:
        typer.secho(f"  ok      {modulo}", fg=VERDE)
    for modulo in resultado.modulos_falhos:
        typer.secho(f"  falhou  {modulo}", fg=VERMELHO)
    if registrar_bronze:
        typer.echo(
            f"  bronze registrado: {resultado.arquivos_bronze} arquivo(s), "
            f"{resultado.concursos_enfileirados} concurso(s)"
        )
    if resultado.seed:
        _titulo("Seed de public.tipo_jogo")
        for jogo in resultado.seed:
            typer.echo(
                f"  id={jogo.id_tipo_jogo:>2} | {jogo.codigo:<12} | {jogo.nome_exibicao:<12} | "
                f"sorteadas={jogo.dezenas_sorteadas:>2} | apostadas={jogo.dezenas_apostadas:>2} | "
                f"disponiveis={jogo.dezenas_disponiveis:>3}"
            )
    if resultado.modulos_falhos:
        raise typer.Exit(code=1)


@app_db.command("limpar")
def db_limpar(
    confirmar: Annotated[
        bool, typer.Option("--confirmar", help="Obrigatorio: a operacao apaga tudo")
    ] = False,
) -> None:
    try:
        removidas = mod_banco.limpar(confirmar=confirmar)
    except mod_banco.ConfirmacaoAusente as e:
        _abortar(str(e))
        return
    typer.secho(f"{len(removidas)} objeto(s) removido(s)", fg=VERDE)


@app_db.command("views-aplicar")
def db_views_aplicar() -> None:
    views = mod_banco.views_aplicar()
    typer.secho(f"{len(views)} view(s) aplicada(s)", fg=VERDE)
    for view in views:
        typer.echo(f"  {view}")


@app.command("saude")
def saude(
    modalidade: OpcaoModalidade = None,
    completo: Annotated[
        bool, typer.Option("--completo", help="Mostra tambem as colunas e constraints conferidas")
    ] = False,
) -> None:
    diagnostico = mod_saude.verificar(modalidade)

    _titulo("Cobertura")
    typer.echo(f"{'modalidade':<14} {'concursos':>10} {'ultimo':>8} {'atraso':>7}  situacao")
    for c in diagnostico.cobertura:
        cor = VERMELHO if c.situacao == "ATRASADO" else None
        typer.secho(
            f"{c.modalidade:<14} {c.total_concursos:>10,} {c.ultimo_concurso or 0:>8} "
            f"{c.atraso_dias or 0:>7}  {c.situacao}",
            fg=cor,
        )

    consistencia = diagnostico.consistencia
    _titulo("Consistencia dos concursos")
    lacunas = consistencia.lacunas_por_modalidade
    if lacunas:
        for m, n in sorted(lacunas.items()):
            typer.secho(f"  {m:<14} {n} concurso(s) faltando no meio da sequencia", fg=VERMELHO)
    else:
        typer.secho("  sem lacunas na sequencia", fg=VERDE)
    cor_anterior = VERMELHO if consistencia.concursos_sem_anterior else None
    typer.secho(
        f"  concursos sem numero_concurso_anterior: {consistencia.concursos_sem_anterior}",
        fg=cor_anterior,
    )
    typer.secho(
        f"  data do proximo ausente ou impossivel:  {consistencia.data_proximo_invalida}",
        fg=VERMELHO if consistencia.data_proximo_invalida else None,
    )

    nomes = consistencia.nomes
    _titulo("Nomes de municipio e de local do sorteio")
    typer.secho(
        f"  localidades a corrigir:                 {nomes.localidades}",
        fg=VERMELHO if nomes.localidades else None,
    )
    typer.secho(
        f"  locais de sorteio a unificar:           {nomes.locais_sorteio}",
        fg=VERMELHO if nomes.locais_sorteio else None,
    )
    typer.echo(f"  fora do IBGE, sem correcao segura:      {', '.join(nomes.fora_do_ibge) or '-'}")
    typer.echo(
        f"  locais fora do catalogo do YAML:        {', '.join(nomes.locais_fora_do_catalogo) or '-'}"
    )

    integridade = diagnostico.integridade
    _titulo("Integridade do schema")
    if integridade.ok:
        typer.secho(f"  {len(integridade.tabelas)} tabela(s) conferidas, tudo certo", fg=VERDE)
    for nome in integridade.ausentes:
        typer.secho(f"  {nome}: TABELA AUSENTE", fg=VERMELHO)
    for tabela in integridade.com_coluna_faltando:
        typer.secho(f"  {tabela.nome}: faltam {', '.join(tabela.colunas_faltando)}", fg=VERMELHO)
    for nome in integridade.sem_unicidade:
        typer.secho(f"  {nome}: sem constraint de unicidade", fg=VERMELHO)
    for tabela in integridade.com_duplicata:
        typer.secho(f"  {tabela.nome}: {tabela.duplicatas} grupo(s) duplicados", fg=VERMELHO)
    if completo:
        for tabela in integridade.tabelas:
            if tabela.existe:
                typer.echo(f"  {tabela.nome:<20} {'; '.join(tabela.unicidade) or 'sem unicidade'}")

    if diagnostico.degradado:
        typer.echo("")
        for motivo in diagnostico.motivos:
            typer.secho(f"[!] {motivo}", fg=AMARELO)
        raise typer.Exit(code=1)
    typer.secho("\npipeline saudavel", fg=VERDE)


@app_dados.command("backup")
def dados_backup(
    destino: Annotated[str, typer.Option("--destino")] = "data-backup",
    limpar_origem: Annotated[bool, typer.Option("--limpar-origem")] = False,
) -> None:
    resultado = mod_dados.backup(destino=destino, limpar_origem=limpar_origem)
    typer.secho(
        f"{resultado.arquivos} arquivo(s), {mod_dados.formatar_bytes(resultado.bytes)} "
        f"-> {resultado.destino}",
        fg=VERDE,
    )
    if resultado.origem_limpa:
        typer.echo(f"  origem esvaziada: {resultado.origem}")


@app_dados.command("catalogo-gerar")
def dados_catalogo_gerar() -> None:
    destino = mod_catalogo.gerar()
    typer.secho(f"catalogo gerado: {destino}", fg=VERDE)


if __name__ == "__main__":
    app()
