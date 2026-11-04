-- 血缘查询③：原始页面/第三方源记录 ID → 受影响的最终实体与字段
-- 用法: psql -d etl_movie -f lineage/query_03_source_to_entities.sql
\set source_record_id 'sr_page_B00005JPS8'

-- 1) 源记录本体
SELECT 'SOURCE' AS section, s.* FROM lineage_source_record s
WHERE s.source_record_id = :'source_record_id';

-- 2) 受影响实体/字段（含是否进入最终值）
SELECT 'IMPACT' AS section, a.entity_type, a.entity_id, a.field_name, a.value_role,
       a.raw_value, a.normalized_value, a.status,
       (f.selected_assertion_uids IS NOT NULL
        AND to_jsonb(a.assertion_uid) <@ f.selected_assertion_uids) AS in_final,
       f.decision_reason
FROM lineage_field_assertion a
LEFT JOIN lineage_field_final f
  ON f.entity_type=a.entity_type AND f.entity_id=a.entity_id AND f.field_name=a.field_name
WHERE a.source_record_id = :'source_record_id'
ORDER BY a.entity_id, a.field_name;

-- 3) 汇总
SELECT 'SUMMARY' AS section, count(*) AS assertion_rows,
       count(DISTINCT a.entity_type || '|' || a.entity_id) AS entities,
       count(DISTINCT a.field_name) AS fields
FROM lineage_field_assertion a WHERE a.source_record_id = :'source_record_id';
