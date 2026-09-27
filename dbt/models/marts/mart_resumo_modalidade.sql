with concursos as (
    select
        modalidade,
        modalidade_nome,
        count(*)                                as total_concursos,
        min(numero_concurso)                    as primeiro_concurso,
        max(numero_concurso)                    as ultimo_concurso,
        min(data_apuracao)                      as primeira_apuracao,
        max(data_apuracao)                      as ultima_apuracao,
        sum(valor_arrecadado)                   as total_arrecadado,
        count(*) filter (where acumulado)       as concursos_acumulados
    from {{ ref('stg_concurso') }}
    group by 1, 2
),

premios as (
    select
        c.modalidade,
        sum(r.valor_total)                      as total_pago_premios,
        sum(r.numero_ganhadores)                as total_ganhadores,
        max(r.valor_premio)                     as maior_premio_individual
    from {{ ref('stg_rateio') }} r
    join {{ ref('stg_concurso') }} c using (id_concurso)
    group by 1
)

select
    c.modalidade,
    c.modalidade_nome,
    c.total_concursos,
    c.primeiro_concurso,
    c.ultimo_concurso,
    c.primeira_apuracao,
    c.ultima_apuracao,
    c.concursos_acumulados,
    round(100.0 * c.concursos_acumulados / nullif(c.total_concursos, 0), 1) as pct_acumulados,
    c.total_arrecadado,
    coalesce(p.total_pago_premios, 0)      as total_pago_premios,
    coalesce(p.total_ganhadores, 0)        as total_ganhadores,
    coalesce(p.maior_premio_individual, 0) as maior_premio_individual,
    round(
        100.0 * coalesce(p.total_pago_premios, 0) / nullif(c.total_arrecadado, 0), 1
    ) as pct_retorno
from concursos c
left join premios p using (modalidade)
