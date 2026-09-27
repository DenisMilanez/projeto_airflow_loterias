select
    r.id_rateio,
    r.id_concurso,
    r.id_faixa,
    f.id_tipo_jogo,
    f.numero_faixa,
    f.acertos,
    f.descricao                       as faixa_descricao,
    coalesce(r.numero_ganhadores, 0)  as numero_ganhadores,
    coalesce(r.valor_premio, 0)       as valor_premio,
    coalesce(r.valor_total, 0)        as valor_total
from {{ source('gold', 'rateio') }} r
join {{ source('gold', 'faixa') }} f using (id_faixa)
