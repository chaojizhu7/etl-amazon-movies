-- 任务6 缺失上映信息补全落库（字段覆盖矩阵 §12 语义；报告 4.3/6.3）
-- 幂等：CREATE IF NOT EXISTS + upsert（run_id 追加）
-- 执行：src/complete_release_info.py

CREATE TABLE IF NOT EXISTS release_facts (
    id              BIGSERIAL PRIMARY KEY,
    movie_id        TEXT NOT NULL REFERENCES movie_entity(movie_id),
    asin            TEXT NOT NULL DEFAULT '',   -- edition 级事实填 ASIN；作品级为 ''
    fact_type       TEXT NOT NULL
        CHECK (fact_type IN ('release_year','regional_release_date','edition_sale_date',
                             'earliest_review_time','inferred_release_bound')),
    value_raw       TEXT,                       -- 原始值（原文保留，如 "2008" / "May 22, 2012"）
    value_norm      TEXT,                       -- 规范值：YYYY 或 ISO 日期；仅年份不补 1 月 1 日
    precision       TEXT
        CHECK (precision IN ('year','day','none')),
    source          TEXT NOT NULL
        CHECK (source IN ('imdb','product_page','review','system')),
    source_locator  TEXT,                       -- tconst / asin+快照哈希 / review 记录定位
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    match_rule      TEXT NOT NULL,
    confidence      NUMERIC(3,2),
    status          TEXT NOT NULL
        CHECK (status IN ('verified','inferred','missing')),
    missing_reason  TEXT
        CHECK (missing_reason IS NULL OR missing_reason IN
               ('no_imdb_match','year_missing_in_imdb','imdb_provides_year_only',
                'no_imdb_crew','not_in_principals','page_unreachable','no_source_genre',
                'empty_user','pending_review','inferred_unverified')),
    run_id          TEXT NOT NULL,
    UNIQUE (movie_id, asin, fact_type, run_id)
);
CREATE INDEX IF NOT EXISTS idx_rf_movie ON release_facts (movie_id);
CREATE INDEX IF NOT EXISTS idx_rf_type ON release_facts (fact_type, status);
CREATE INDEX IF NOT EXISTS idx_rf_asin ON release_facts (asin);
