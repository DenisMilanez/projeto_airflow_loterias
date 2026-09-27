with rateio as (
    select
        c.modalidade,
        r.acertos,
        r.numero_faixa,
        r.faixa_descricao,
        r.numero_ganhadores,
        r.valor_premio,
        r.valor_total
    from {{ ref('stg_rateio') }} r
    join {{ ref('stg_concurso') }} c using (id_concurso)
),

por_acertos as (
    select
        modalidade,
        acertos,
        min(numero_faixa)                             as numero_faixa,
        max(faixa_descricao)                          as faixa_descricao,
        count(*)                                      as concursos_avaliados,
        count(*) filter (where numero_ganhadores > 0) as concursos_com_ganhador,
        sum(numero_ganhadores)                        as total_ganhadores,
        sum(valor_total)                              as total_pago,
        max(valor_premio)                             as maior_premio
    from rateio
    group by 1, 2
),

ganhadores_acumulados as (
    select
        modalidade,
        acertos,
        valor_premio,
        sum(numero_ganhadores) over (
            partition by modalidade, acertos
            order by valor_premio
            rows between unbounded preceding and current row
        ) as ganhadores_ate_aqui,
        sum(numero_ganhadores) over (partition by modalidade, acertos) as ganhadores_na_faixa
    from rateio
    where numero_ganhadores > 0
),

mediana as (
    select
        modalidade,
        acertos,
        min(valor_premio) as premio_mediano_por_ganhador
    from ganhadores_acumulados
    where ganhadores_ate_aqui >= ganhadores_na_faixa / 2.0
    group by 1, 2
)

select
    p.modalidade,
    p.acertos,
    p.numero_faixa,
    p.faixa_descricao,
    p.concursos_avaliados,
    p.concursos_com_ganhador,
    p.total_ganhadores,
    p.total_pago,
    round(p.total_pago / nullif(p.total_ganhadores, 0), 2) as premio_medio_por_ganhador,
    m.premio_mediano_por_ganhador,
    p.maior_premio
from por_acertos p
left join mediana m using (modalidade, acertos)
