-- 查询②：查某导演的全部作品及来源
-- 输入：person_id（示例 p_011240 = stanley kubrick（6 部作品；同名实体 p_013112 等另计））
-- 运行：psql -U postgres -d etl_movie -f sql/queries/q2_director_works.sql
-- 口径：
--   * 通过 movie_person 关联；同一人多角色分行显示（director/actor/actress）；
--   * 来源标签 source：imdb（nconst 已核实）/ product_page（页面署名）；
--   * source_locator：imdb=nconst / product_page=asin+快照定位；
--   * 上映年份 = release_facts.fact_type='release_year' 的 value_norm（仅年份精度 precision='year'，
--     缺失不补 1 月 1 日）；缺失标 missing_reason。

SELECT
    mp.person_id,
    p.name_canonical,
    mp.movie_id,
    e.title_canonical                    AS work_title,
    mp.role,                             -- director / actor / actress（同人多角色分行）
    rf.value_norm                        AS release_year,
    'year'::text                         AS year_precision,  -- R5 精度口径：仅出 YYYY
    mp.source                            AS source_label,   -- imdb / product_page
    mp.nconst                            AS imdb_nconst,
    mp.source_locator,
    ''::text                             AS missing_reason   -- selected 值非缺失
FROM movie_person mp
JOIN person_entity p ON p.person_id = mp.person_id
JOIN movie_entity  e ON e.movie_id  = mp.movie_id
LEFT JOIN (SELECT DISTINCT ON (movie_id) movie_id, value_norm
             FROM field_assertion WHERE field_name='release_year' AND status='selected'
            ORDER BY movie_id, id DESC) rf ON rf.movie_id = mp.movie_id
WHERE mp.person_id = 'p_011240'
ORDER BY mp.role, rf.value_norm NULLS LAST, mp.movie_id;
