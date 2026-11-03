-- product_classification: 任务3 电影页面筛选分类结果
-- 规则见 rules/movie_classification_v0.1.md（每行带 rules_version + run_id 可回溯）
-- Idempotent: CREATE IF NOT EXISTS + upsert 语义（见 src/classify_products.py）

CREATE TABLE IF NOT EXISTS product_classification (
    asin                  TEXT PRIMARY KEY,
    classification        TEXT NOT NULL
        CHECK (classification IN ('movie','non_movie','collection','tv_series',
                                  'concert','other','pending_review')),
    classification_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (classification_status IN ('evidence','inferred','pending')),
    confidence            NUMERIC(3,2)
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    pending_reason        TEXT
        CHECK (pending_reason IS NULL OR pending_reason IN
               ('not_crawled','access_restricted','fetch_failed','page_unparsed',
                'evidence_insufficient','signal_conflict','imdb_ambiguous')),
    page_title_raw        TEXT,                  -- productPage 原始标题（source=product_page）
    snapshot_sha256       TEXT,                  -- 快照内容哈希（source=product_page 定位）
    candidate_tconsts     TEXT[],                -- IMDb 候选（source=imdb）
    imdb_match_status     TEXT
        CHECK (imdb_match_status IS NULL OR imdb_match_status IN
               ('unique','unique_class','ambiguous','none','not_attempted')),
    imdb_title_type       TEXT,                  -- 唯一/唯一类匹配时的 titleType（source=imdb）
    evidence              JSONB NOT NULL,        -- 信号明细 + 触发规则 + 理由
    rules_version         TEXT NOT NULL,
    run_id                TEXT NOT NULL,
    classified_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_class_class ON product_classification (classification, classification_status);
CREATE INDEX IF NOT EXISTS idx_class_run ON product_classification (run_id);
CREATE INDEX IF NOT EXISTS idx_class_reason ON product_classification (pending_reason);

-- product_movie_candidate: 合集（collection）候选多电影映射
-- 任务4 ProductMovie 多对多的预留；仅登记候选标题与来源位置，不生成 movie_id
CREATE TABLE IF NOT EXISTS product_movie_candidate (
    asin              TEXT NOT NULL,
    movie_title_raw   TEXT NOT NULL,             -- 产品页列出的候选影片标题原文
    match_source      TEXT NOT NULL              -- page_genre_list / page_title / page_description
        CHECK (match_source IN ('page_genre_list','page_title','page_description')),
    candidate_tconsts TEXT[],                    -- 与该候选标题精确匹配的 IMDb 候选（可空）
    rules_version     TEXT NOT NULL,
    run_id            TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (asin, movie_title_raw)
);

CREATE INDEX IF NOT EXISTS idx_pmc_asin ON product_movie_candidate (asin);
