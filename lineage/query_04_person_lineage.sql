-- 血缘查询④：person_id → 原始姓名、别名归并过程、参与作品
-- 用法: psql -d etl_movie -f lineage/query_04_person_lineage.sql
\set person_id 'p_000001'

-- 1) 实体 + 原始姓名/别名（原文保留）
SELECT 'ALIAS' AS section, pe.person_id, pe.name_canonical, pe.external_ids,
       pe.primary_occupation, pe.era_span,
       pa.name_raw, pa.source, pa.source_locator
FROM person_entity pe
LEFT JOIN person_alias pa ON pa.person_id = pe.person_id
WHERE pe.person_id = :'person_id'
ORDER BY pa.source, pa.name_raw;

-- 2) 别名归并过程（处理链：按该 person 的节点键 nm*/pg:* 匹配）
SELECT DISTINCT 'MERGE_PROCESS' AS section, p.step_id, p.op_type, p.target_ref, p.rule_id, p.run_id, p.note
FROM lineage_process_step p
JOIN person_alias pa ON pa.person_id = :'person_id'
WHERE p.target_kind='person_pair'
  AND (p.target_ref LIKE pa.source_locator || '|%' OR p.target_ref LIKE '%|' || pa.source_locator)
ORDER BY p.step_id;

-- 3) 参与作品（角色独立证据：每作品每角色一行）
SELECT 'WORKS' AS section, mp.movie_id, me.title_canonical, mp.role, mp.ordering,
       mp.nconst, mp.source, mp.source_locator
FROM movie_person mp
JOIN movie_entity me ON me.movie_id = mp.movie_id
WHERE mp.person_id = :'person_id'
ORDER BY me.title_canonical, mp.role;
