-- 血缘查询②：ASIN / source_review_id → 对应版本(edition)、作品(movie)、匹配状态
-- 用法: psql -d etl_movie -f lineage/query_02_review_to_movie.sql
\set asin 'B00005JPS8'
\set record_ordinal '1'

-- 1) ASIN → 版本 → 作品 → 分类/匹配状态
SELECT 'EDITION' AS section, pm.asin, pm.movie_id, pm.relation_type, pm.match_basis,
       me.title_canonical, me.status AS movie_status,
       pc.classification, pc.classification_status,
       pc.evidence->'imdb_signals'->>'match_status' AS imdb_match,
       pc.evidence->'imdb_signals'->'resolution'->>'method' AS resolve_method
FROM product_movie pm
JOIN movie_entity me ON me.movie_id = pm.movie_id
LEFT JOIN product_classification pc ON pc.asin = pm.asin
WHERE pm.asin = :'asin';

-- 2) source_review_id（asin+记录序号）→ 原始评论记录 → 同一链路
SELECT 'REVIEW' AS section, rri.asin, rri.record_ordinal, rri.user_id, rri.review_time,
       rri.score, rm.logical_id, rm.map_status,
       pm.movie_id, me.title_canonical
FROM review_record_index rri
LEFT JOIN review_mapping rm ON rm.asin=rri.asin AND rm.record_ordinal=rri.record_ordinal
LEFT JOIN product_movie pm ON pm.asin = rri.asin AND pm.relation_type='single'
LEFT JOIN movie_entity me ON me.movie_id = pm.movie_id
WHERE rri.asin = :'asin' AND rri.record_ordinal = :record_ordinal;

-- 3) 该 ASIN 全部评论定位（计数）
SELECT 'REVIEW_COUNT' AS section, count(*) AS records, count(DISTINCT logical_id) AS logical_reviews
FROM review_mapping WHERE asin = :'asin';
