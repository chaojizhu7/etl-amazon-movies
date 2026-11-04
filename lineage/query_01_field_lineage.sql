-- 血缘查询①：movie_id + 字段名 → 最终值、被采用/未采用候选、原始证据、选择理由
-- 用法: psql -d etl_movie -f lineage/query_01_field_lineage.sql
\set movie_id 'mv_000198'
\set field_name 'release_year'

-- 1) 最终值（可解释）
SELECT 'FINAL' AS section, f.entity_id, f.field_name,
       f.selected_assertion_uids, f.decision_reason, f.rules_version
FROM lineage_field_final f
WHERE f.entity_type='movie' AND f.entity_id = :'movie_id' AND f.field_name = :'field_name';

-- 2) 全部候选（含未采用）+ 原始证据 + 状态
SELECT 'CANDIDATE' AS section, a.assertion_uid, a.value_role, a.raw_value, a.normalized_value,
       a.status, a.confidence, a.extraction_rule,
       a.source, s.source_address, s.snapshot_sha256, a.field_locator
FROM lineage_field_assertion a
LEFT JOIN lineage_source_record s ON s.source_record_id = a.source_record_id
WHERE a.entity_type='movie' AND a.entity_id = :'movie_id' AND a.field_name = :'field_name'
ORDER BY a.status, a.assertion_uid;

-- 3) 相关处理链（选择/合并步骤）
SELECT 'STEP' AS section, p.step_id, p.op_type, p.rule_id, p.run_id, p.confidence, p.note
FROM lineage_process_step p
WHERE p.target_ref LIKE :'movie_id' || '|%'
ORDER BY p.step_id;
