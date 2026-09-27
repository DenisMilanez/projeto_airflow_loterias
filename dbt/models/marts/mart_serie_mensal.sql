with base as (
    select
        c.modalidade,
        c.mes_apuracao,
        count(*)                  as concursos,
        sum(c.valor_arrecadado)   as arrecadado
    from {{ ref('stg_concurso') }} c
    group by 1, 2
),

ganhadores as (
    select
        c.modalidade,
        c.mes_apuracao,
        sum(r.numero_ganhadores) as ganhadores,
        sum(r.valor_total)       as pago_premios
    from {{ ref('stg_rateio') }} r
    join {{ ref('stg_concurso') }} c using (id_concurso)
    group by 1, 2
)

select
    b.modalidade,
    b.mes_apuracao,
    b.concursos,
    b.arrecadado,
    coalesce(g.ganhadores, 0)   as ganhadores,
    coalesce(g.pago_premios, 0) as pago_premios,
    sum(b.arrecadado) over (
        partition by b.modalidade order by b.mes_apuracao
    ) as arrecadado_acumulado
from base b
left join ganhadores g using (modalidade, mes_apuracao)
