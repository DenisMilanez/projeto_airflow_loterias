with fonte as (
    select * from {{ source('gold', 'concurso') }}
)

select
    c.id_concurso,
    c.id_tipo_jogo,
    tj.codigo                                as modalidade,
    tj.nome_exibicao                         as modalidade_nome,
    c.numero_concurso,
    c.data_apuracao,
    date_trunc('month', c.data_apuracao)::date as mes_apuracao,
    extract(year from c.data_apuracao)::int  as ano_apuracao,
    c.acumulado,
    coalesce(c.valor_arrecadado, 0)          as valor_arrecadado,
    coalesce(c.valor_acumulado_proximo_concurso, 0) as valor_acumulado_proximo
from fonte c
join {{ source('gold', 'tipo_jogo') }} tj using (id_tipo_jogo)
where c.data_apuracao is not null
