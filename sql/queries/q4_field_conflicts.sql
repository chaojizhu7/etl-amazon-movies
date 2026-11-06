-- 查询④：字段冲突与选择理由（conflict 全量清单）
-- 运行：psql -U postgres -d etl_movie -f sql/queries/q4_field_conflicts.sql
-- 口径：
--   * 仅列 status='conflict' 的字段断言；conflict 状态**不编造答案**——保留候选、待人工决策；
--   * 每个候选含 raw_value、source（imdb/product_page/review/system）、source_locator、
--     match_rule、decision_basis、reject_reason（拒绝者必附词表理由）；
--   * 最终选择：field_selection_rules v1.0（R1 语义边界→R2 匹配可靠→R3 来源权威→R4 语义一致
--     →R5 日期精度→R6 采集时间仅平局→R7 人工判定继承）能选出则 lineage_field_final 记
--     selected_assertion_uids；选不出维持 conflict（显示"保留冲突"）；
--   * entity 域为电影作品（field_assertion.movie_id）；人物域断言同规则在 person 域复用。

SELECT
    'movie'::text              AS entity_type,
    fa.movie_id                AS entity_id,
    e.title_canonical          AS entity_title,
    fa.field_name,
    fa.value_role,
    fa.value_raw,
    fa.value_norm AS normalized_value,
    fa.source,
    fa.source_locator,
    fa.match_rule,
    fa.confidence,
    fa.decision_basis,
    fa.reject_reason,
    COALESCE('selected: ' || ff.selected_assertion_uids::text,
             '保留冲突（待人工决策）') AS final_selection
FROM field_assertion fa
LEFT JOIN movie_entity e ON e.movie_id = fa.movie_id
LEFT JOIN lineage_field_final ff
       ON ff.entity_type = 'movie' AND ff.entity_id = fa.movie_id
      AND ff.field_name = fa.field_name
WHERE fa.status = 'conflict'
ORDER BY fa.movie_id, fa.field_name, fa.source;

-- 4b. 冲突统计（按字段/来源）
SELECT field_name, source, count(*) AS n_conflict_candidates,
       count(DISTINCT movie_id) AS n_entities
FROM field_assertion WHERE status='conflict'
GROUP BY 1,2 ORDER BY 1,2;

-- 4c. 冲突单元统计（movie×field 去重）
SELECT count(DISTINCT (movie_id, field_name)) AS n_conflict_units,
       count(*) AS n_candidate_rows
FROM field_assertion WHERE status='conflict';
