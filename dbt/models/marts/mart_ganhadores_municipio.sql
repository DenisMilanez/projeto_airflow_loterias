select
    g.uf,
    g.municipio,
    g.canal_eletronico,
    g.municipio <> 'NAO INFORMADO' and not g.canal_eletronico as municipio_identificado,
    c.modalidade,
    count(distinct g.id_concurso)  as concursos_com_ganhador,
    sum(g.numero_ganhadores)       as total_ganhadores,
    min(c.data_apuracao)           as primeiro_ganho,
    max(c.data_apuracao)           as ultimo_ganho
from {{ ref('stg_ganhador_municipio') }} g
join {{ ref('stg_concurso') }} c using (id_concurso)
group by 1, 2, 3, 4, 5
