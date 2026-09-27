select
    c.modalidade,
    r.numero_faixa,
    r.acertos,
    max(r.faixa_descricao)                                   as faixa_descricao,
    count(*)                                                 as concursos_avaliados,
    count(*) filter (where r.numero_ganhadores > 0)          as concursos_com_ganhador,
    sum(r.numero_ganhadores)                                 as total_ganhadores,
    sum(r.valor_total)                                       as total_pago,
    round(avg(r.valor_premio) filter (where r.numero_ganhadores > 0), 2) as premio_medio,
    max(r.valor_premio)                                      as maior_premio
from {{ ref('stg_rateio') }} r
join {{ ref('stg_concurso') }} c using (id_concurso)
group by 1, 2, 3
