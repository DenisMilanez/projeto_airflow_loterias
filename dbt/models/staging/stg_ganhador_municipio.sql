select
    gm.id_ganhador_municipio,
    gm.id_concurso,
    gm.id_localidade,
    loc.municipio,
    loc.uf,
    loc.uf = '--'                     as canal_eletronico,
    coalesce(gm.numero_ganhadores, 0) as numero_ganhadores
from {{ source('gold', 'ganhador_municipio') }} gm
join {{ source('gold', 'localidade') }} loc using (id_localidade)
