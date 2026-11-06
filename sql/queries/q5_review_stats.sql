-- 查询⑤：按产品/作品统计评论数量与评分
-- 输入：movie_id（示例 mv_000006）
-- 运行：psql -U postgres -d etl_movie -f sql/queries/q5_review_stats.sql
-- ⚠️ 口径（本查询所有数字必须按此解释）：
--   1) 合集评论归属：合集 ASIN 的评论归合集商品，不拆分/不复制给成员电影
--      （ProductMovie 多对多：成员电影的统计只含其自身 edition 的评论）；
--   2) 重复评论计数：原始记录 7,911,684 中 5,146 条为同一逻辑评论的重复副本，
--      logical_id 映射去重后只计 1 次（全库逻辑评论 7,906,538）；
--   3) 版本合并分母：同一 movie_id 多 edition 评论合并计算，
--      分母 = 逻辑评论数（不含副本）；平均分 = 各逻辑评论 score 均值；
--   4) helpfulness 只校验不加权（分子≤分母、票数≥0，违反记 review_anomaly），
--      不参与评分计算；
--   5) 跨版本相似评论（同一用户在不同 edition 上的相似文本）全部保留不删，
--      仅统计（全库 1,239,387 组 / 7,240,056 条），本查询不去重这类。

-- 5a. 该作品全部 edition 与逐版评论统计
SELECT
    pm.asin                                   AS edition_id,
    pc.page_title_raw                         AS page_title,
    pm.relation_type,
    count(DISTINCT ri.record_ordinal)         AS raw_review_records,
    count(DISTINCT rm.logical_id)             AS logical_reviews,   -- 去重副本后
    round(avg(ri.score)::numeric, 3)          AS avg_score_raw,
    round(avg(ri.score) FILTER (WHERE rm.map_status <> 'duplicate_copy')::numeric, 3) AS avg_score_logical,
    COALESCE(sum(1) FILTER (WHERE rm.map_status = 'duplicate_copy'), 0) AS duplicate_copies
FROM product_movie pm
LEFT JOIN product_classification pc ON pc.asin = pm.asin
LEFT JOIN review_record_index ri ON ri.asin = pm.asin
LEFT JOIN review_mapping rm ON rm.asin = pm.asin AND rm.record_ordinal = ri.record_ordinal
WHERE pm.movie_id = 'mv_000006'
GROUP BY pm.asin, pc.page_title_raw, pm.relation_type
ORDER BY pm.asin;

-- 5b. 该作品汇总（版本合并口径）
SELECT
    'mv_000006'                               AS movie_id,
    count(DISTINCT ri.record_ordinal)         AS raw_review_records,
    count(DISTINCT rm.logical_id)             AS logical_reviews,   -- 分母=逻辑评论数
    round(avg(ri.score) FILTER (WHERE rm.map_status <> 'duplicate_copy')::numeric, 3) AS avg_score
FROM product_movie pm
JOIN review_record_index ri ON ri.asin = pm.asin
LEFT JOIN review_mapping rm ON rm.asin = pm.asin AND rm.record_ordinal = ri.record_ordinal
WHERE pm.movie_id = 'mv_000006';

-- 5c. 全库口径核对（声明数）
SELECT
    (SELECT count(*) FROM review_record_index)                       AS raw_records,
    (SELECT count(DISTINCT logical_id) FROM review_mapping)          AS logical_reviews,
    (SELECT count(*) FROM review_anomaly)                            AS helpfulness_anomalies_logged;
