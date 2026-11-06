-- 查询①：查某作品的所有商品版本（edition 列表）
-- 输入：movie_id（示例 mv_000006 = A Christmas Carol 1951 / Scrooge 人工合并簇）
-- 运行：psql -U postgres -d etl_movie -f sql/queries/q1_work_editions.sql
-- 口径：
--   * edition_id = ASIN（产品版本）；同一 movie_id 下多版本共享作品实体，edition 各自保留；
--   * 载体/地区码/剪辑版/语言字幕 = 从产品页标题解析（page_title_raw 模式匹配，raw 保留原文）；
--     发行商：本数据集产品页无结构化发行商字段，留空并声明（不编造）；
--   * 发售日期 = release_facts.fact_type='edition_sale_date'（产品页发售语义，≠上映日期）；
--   * 合集成员关系：relation_type='collection_member' 表示该 edition 同时属于合集商品
--     （合集评论归合集 ASIN，不拆分给成员电影——见查询⑤口径）。

SELECT
    pm.asin                                   AS edition_id,
    pc.page_title_raw                         AS page_title,
    CASE
        WHEN pc.page_title_raw ~* 'blu-?ray|bd\b'        THEN 'Blu-ray'
        WHEN pc.page_title_raw ~* 'dvd'                  THEN 'DVD'
        WHEN pc.page_title_raw ~* 'vhs'                  THEN 'VHS'
        WHEN pc.page_title_raw ~* 'hd dvd'               THEN 'HD DVD'
        WHEN pc.page_title_raw ~* 'umd|psp'              THEN 'UMD/PSP'
        ELSE 'other/unspecified'
    END                                       AS media_format,
    CASE
        WHEN pc.page_title_raw ~* 'region\s*1'  THEN 'Region 1'
        WHEN pc.page_title_raw ~* 'region\s*2'  THEN 'Region 2'
        WHEN pc.page_title_raw ~* 'ntsc'        THEN 'NTSC'
        WHEN pc.page_title_raw ~* 'pal\b'       THEN 'PAL'
        ELSE 'unspecified'
    END                                       AS region_code,
    CASE
        WHEN pc.page_title_raw ~* 'director.{0,3}cut'  THEN 'Directors Cut'
        WHEN pc.page_title_raw ~* 'unrated|uncut'        THEN 'Unrated/Uncut'
        WHEN pc.page_title_raw ~* 'extended'             THEN 'Extended'
        WHEN pc.page_title_raw ~* 'special edition'      THEN 'Special Edition'
        WHEN pc.page_title_raw ~* 'anniversary'          THEN 'Anniversary Edition'
        ELSE 'theatrical/standard'
    END                                       AS cut_variant,
    CASE
        WHEN pc.page_title_raw ~* 'spanish|espanol|import-latin' THEN 'Spanish/LatAm'
        WHEN pc.page_title_raw ~* 'french'                      THEN 'French'
        WHEN pc.page_title_raw ~* 'german'                      THEN 'German'
        WHEN pc.page_title_raw ~* 'japan'                       THEN 'Japanese'
        ELSE 'English/unspecified'
    END                                       AS language_hint,
    NULL::text                                AS publisher,   -- 数据集无结构化发行商字段（不编造）
    rf.value_norm                             AS edition_sale_date,
    pm.relation_type                          AS collection_membership,
    pm.match_basis                            AS match_basis
FROM product_movie pm
LEFT JOIN product_classification pc ON pc.asin = pm.asin
LEFT JOIN (SELECT DISTINCT ON (movie_id, asin) movie_id, asin, value_norm
             FROM release_facts WHERE fact_type='edition_sale_date' AND status<>'missing'
            ORDER BY movie_id, asin, extracted_at DESC) rf
       ON rf.movie_id = pm.movie_id AND rf.asin = pm.asin
WHERE pm.movie_id = 'mv_000006'
ORDER BY pm.relation_type, pm.asin;
