-- 查询③：缺失与推断上映信息列表（全量）
-- 运行：psql -U postgres -d etl_movie -f sql/queries/q3_release_missing_inferred.sql
-- 口径：
--   * 三类分开不合并统计：missing（NULL+missing_reason）/ inferred（status=inferred，
--     含 inferred_release_bound 条件上界）/ verified（已核实，仅参照不计入缺失）；
--   * release_year 精度只出 YYYY（precision='year'），不得补 1 月 1 日；
--   * inferred_release_bound = 最早评论时间等条件推断的上界（status=inferred，含 confidence）；
--   * missing_reason 词表：no_imdb_match / year_missing_in_imdb / imdb_provides_year_only /
--     no_imdb_crew / not_in_principals / page_unreachable / no_source_genre /
--     empty_user / pending_review / inferred_unverified。

-- 3a. 缺失（release_year 无值或 status=missing）
SELECT 'missing' AS class, m.movie_id, m.title_canonical,
       rf.value_norm AS release_year, rf.precision,
       rf.missing_reason, NULL::text AS inferred_release_bound,
       rf.confidence, rf.status, rf.source
FROM movie_entity m
JOIN release_facts rf ON rf.movie_id = m.movie_id AND rf.asin = ''
WHERE rf.fact_type = 'release_year'
  AND (rf.value_norm IS NULL OR rf.status = 'missing')
ORDER BY m.movie_id;

-- 3b. 推断（status=inferred；含条件上界）
SELECT 'inferred' AS class, m.movie_id, m.title_canonical,
       rf.value_norm AS release_year, rf.precision,
       rf.missing_reason,
       (SELECT b.value_norm FROM release_facts b
         WHERE b.movie_id = m.movie_id AND b.asin = ''
           AND b.fact_type = 'inferred_release_bound' LIMIT 1) AS inferred_release_bound,
       rf.confidence, rf.status, rf.source
FROM movie_entity m
JOIN release_facts rf ON rf.movie_id = m.movie_id AND rf.asin = ''
WHERE rf.fact_type = 'release_year' AND rf.status = 'inferred'
ORDER BY rf.confidence NULLS LAST, m.movie_id;

-- 3c. 三分统计（不合并口径）
SELECT
    count(*) FILTER (WHERE rf.status='missing' OR rf.value_norm IS NULL)  AS n_missing,
    count(*) FILTER (WHERE rf.status='inferred')                          AS n_inferred,
    count(*) FILTER (WHERE rf.status='verified')                          AS n_verified
FROM release_facts rf WHERE rf.asin='' AND rf.fact_type='release_year';
