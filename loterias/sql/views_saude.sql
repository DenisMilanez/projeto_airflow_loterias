CREATE OR REPLACE VIEW pipeline.v_saude_cobertura AS
WITH ult AS (
    SELECT DISTINCT ON (id_tipo_jogo)
        id_tipo_jogo, numero_concurso, data_apuracao, data_proximo_concurso
    FROM public.concurso
    ORDER BY id_tipo_jogo, numero_concurso DESC
)
SELECT
    tj.codigo                                   AS modalidade,
    tj.nome_exibicao,
    COUNT(c.id_concurso)                        AS total_concursos,
    MIN(c.numero_concurso)                      AS primeiro_concurso,
    MAX(c.numero_concurso)                      AS ultimo_concurso,
    ult.data_apuracao                           AS ultima_apuracao,
    ult.data_proximo_concurso                   AS proximo_sorteio_previsto,
    GREATEST(0, CURRENT_DATE - ult.data_proximo_concurso) AS atraso_dias,
    CASE
        WHEN ult.data_proximo_concurso IS NULL              THEN 'sem dados'
        WHEN ult.data_proximo_concurso >= CURRENT_DATE      THEN 'em dia'
        WHEN CURRENT_DATE - ult.data_proximo_concurso <= 1  THEN 'em dia'
        ELSE 'ATRASADO'
    END                                         AS situacao
FROM public.tipo_jogo tj
LEFT JOIN public.concurso c ON c.id_tipo_jogo = tj.id_tipo_jogo
LEFT JOIN ult              ON ult.id_tipo_jogo = tj.id_tipo_jogo
GROUP BY tj.codigo, tj.nome_exibicao, ult.data_apuracao, ult.data_proximo_concurso;

CREATE OR REPLACE VIEW pipeline.v_saude_lacunas AS
WITH faixa AS (
    SELECT id_tipo_jogo, MIN(numero_concurso) AS lo, MAX(numero_concurso) AS hi
    FROM public.concurso
    GROUP BY id_tipo_jogo
),
serie AS (
    SELECT f.id_tipo_jogo, generate_series(f.lo, f.hi) AS n FROM faixa f
)
SELECT
    tj.codigo   AS modalidade,
    s.n         AS concurso_faltando
FROM serie s
JOIN public.tipo_jogo tj ON tj.id_tipo_jogo = s.id_tipo_jogo
LEFT JOIN public.concurso c
       ON c.id_tipo_jogo = s.id_tipo_jogo AND c.numero_concurso = s.n
WHERE c.id_concurso IS NULL;

CREATE OR REPLACE VIEW pipeline.v_saude_filas AS
SELECT 'concursos'::text AS fonte, modalidade, status, COUNT(*) AS qtd
FROM pipeline.concurso_fila
GROUP BY modalidade, status
UNION ALL
SELECT 'locais_sorte'::text AS fonte, modalidade, status, COUNT(*) AS qtd
FROM pipeline.locais_sorte_fila
GROUP BY modalidade, status;

CREATE OR REPLACE VIEW pipeline.v_saude_problemas AS
SELECT
    'locais_sorte'::text AS fonte, modalidade, numero_concurso, status,
    tentativas, max_tentativas, proximo_retry_em,
    LEFT(COALESCE(erro_detalhe, ''), 120) AS detalhe
FROM pipeline.locais_sorte_fila
WHERE status IN ('erro', 'abandonado', 'parcial')
UNION ALL
SELECT
    'concursos'::text AS fonte, modalidade, numero_concurso, status,
    tentativas, max_tentativas, proximo_retry_em,
    LEFT(COALESCE(erro_detalhe, ''), 120) AS detalhe
FROM pipeline.concurso_fila
WHERE status IN ('erro', 'abandonado', 'parcial');

CREATE OR REPLACE VIEW pipeline.v_saude_execucoes AS
SELECT
    id_execucao, modalidade, fonte, tipo, status,
    concurso_inicio, concurso_fim,
    total_coletados, total_erros, total_sem_dados,
    iniciado_em, finalizado_em,
    EXTRACT(EPOCH FROM (finalizado_em - iniciado_em))::int AS duracao_seg
FROM pipeline.execucao;

CREATE OR REPLACE VIEW pipeline.v_saude_volumes AS
SELECT
    tj.codigo AS modalidade,
    (SELECT COUNT(*) FROM public.concurso c
       WHERE c.id_tipo_jogo = tj.id_tipo_jogo) AS concursos,
    (SELECT COUNT(*) FROM public.dezena d JOIN public.concurso c ON c.id_concurso = d.id_concurso
       WHERE c.id_tipo_jogo = tj.id_tipo_jogo) AS dezenas,
    (SELECT COUNT(*) FROM public.rateio r JOIN public.concurso c ON c.id_concurso = r.id_concurso
       WHERE c.id_tipo_jogo = tj.id_tipo_jogo) AS rateios,
    (SELECT COUNT(*) FROM public.ganhador_municipio g JOIN public.concurso c ON c.id_concurso = g.id_concurso
       WHERE c.id_tipo_jogo = tj.id_tipo_jogo) AS ganhadores_municipio,
    (SELECT COUNT(*) FROM public.ganhador_loterica g JOIN public.concurso c ON c.id_concurso = g.id_concurso
       WHERE c.id_tipo_jogo = tj.id_tipo_jogo) AS ganhadores_loterica
FROM public.tipo_jogo tj;

CREATE OR REPLACE VIEW pipeline.v_concurso_detalhe AS
SELECT
    tj.codigo                          AS modalidade,
    c.numero_concurso,
    c.data_apuracao,
    c.data_proximo_concurso,
    c.acumulado,
    c.valor_arrecadado,
    c.valor_estimado_proximo_concurso,
    ls.nome                            AS local_sorteio
FROM public.concurso c
JOIN public.tipo_jogo tj  ON tj.id_tipo_jogo = c.id_tipo_jogo
LEFT JOIN public.local_sorteio ls ON ls.id_local_sorteio = c.id_local_sorteio;

CREATE OR REPLACE VIEW pipeline.v_fila_detalhe AS
SELECT
    'concursos'::text AS fonte, modalidade, numero_concurso, status,
    tentativas, max_tentativas, ultima_tentativa_em, proximo_retry_em,
    LEFT(COALESCE(erro_detalhe, ''), 200) AS detalhe
FROM pipeline.concurso_fila
UNION ALL
SELECT
    'locais_sorte'::text AS fonte, modalidade, numero_concurso, status,
    tentativas, max_tentativas, ultima_tentativa_em, proximo_retry_em,
    LEFT(COALESCE(erro_detalhe, ''), 200) AS detalhe
FROM pipeline.locais_sorte_fila;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metabase') THEN
        CREATE ROLE metabase LOGIN PASSWORD 'metabase';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA pipeline TO metabase;
GRANT SELECT ON ALL TABLES IN SCHEMA pipeline TO metabase;
GRANT USAGE ON SCHEMA public TO metabase;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO metabase;
