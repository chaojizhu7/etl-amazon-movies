-- 任务4 电影实体合并落库（merge_rules_v1.md v1.2）
-- 幂等：CREATE IF NOT EXISTS + 追加式决策（run_id 区分批次，历史不改写）
-- 执行：src/merge_movies.py --execute

CREATE TABLE IF NOT EXISTS movie_entity (
    movie_id        TEXT PRIMARY KEY,           -- 系统生成 mv_<6位序号>（不用 ASIN/tconst）
    title_canonical TEXT NOT NULL,
    external_ids    JSONB NOT NULL DEFAULT '{}', -- {"imdb": ["tt..."]}（来源定位符）
    status          TEXT NOT NULL DEFAULT 'merged'
        CHECK (status IN ('merged','singleton','unresolved_candidate','manual_hold')),
    rules_version   TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS movie_alias (
    id              BIGSERIAL PRIMARY KEY,
    movie_id        TEXT NOT NULL REFERENCES movie_entity(movie_id),
    title_raw       TEXT NOT NULL,               -- 原文保留不改意
    title_canon     TEXT NOT NULL,
    source          TEXT NOT NULL                -- product_page / imdb / review / manual / candidate
        CHECK (source IN ('product_page','imdb','review','manual','candidate')),
    source_locator  TEXT,                        -- asin / tconst / 快照哈希 / manual_review 标记
    run_id          TEXT NOT NULL,
    UNIQUE (movie_id, title_canon, source, source_locator)
);
CREATE INDEX IF NOT EXISTS idx_alias_canon ON movie_alias (title_canon);
CREATE INDEX IF NOT EXISTS idx_alias_movie ON movie_alias (movie_id);

CREATE TABLE IF NOT EXISTS movie_merge_candidate (
    pair_id         BIGSERIAL PRIMARY KEY,
    node_a          TEXT NOT NULL,               -- asin 或 asin::候选标题
    node_b          TEXT NOT NULL,
    node_kind       TEXT NOT NULL DEFAULT 'edition'
        CHECK (node_kind IN ('edition','collection_member')),
    signals         JSONB NOT NULL,              -- 6 信号取值
    title_coeff     NUMERIC(3,2),
    score_v         NUMERIC(4,3),
    score_conf      NUMERIC(4,3),
    known_signals   INTEGER,
    strong_signals  INTEGER,
    rules_version   TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    UNIQUE (node_a, node_b, run_id)
);

CREATE TABLE IF NOT EXISTS movie_merge_decision (
    id              BIGSERIAL PRIMARY KEY,
    node_a          TEXT NOT NULL,
    node_b          TEXT NOT NULL,
    decision        TEXT NOT NULL
        CHECK (decision IN ('auto_merge','manual_merge','manual_review','no_merge')),
    reason          TEXT NOT NULL,
    pair_id         BIGINT,                      -- 可空（无 candidate 行时）
    rules_version   TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_decision_pair ON movie_merge_decision (node_a, node_b);
CREATE INDEX IF NOT EXISTS idx_decision_run ON movie_merge_decision (run_id);

CREATE TABLE IF NOT EXISTS product_movie (
    asin            TEXT NOT NULL,
    movie_id        TEXT NOT NULL REFERENCES movie_entity(movie_id),
    relation_type   TEXT NOT NULL
        CHECK (relation_type IN ('single','collection_member')),
    match_basis     TEXT NOT NULL,               -- auto_merge_cluster / candidate_title / singleton
    evidence        JSONB NOT NULL DEFAULT '{}',
    rules_version   TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (asin, movie_id)
);
CREATE INDEX IF NOT EXISTS idx_pm_movie ON product_movie (movie_id);

CREATE TABLE IF NOT EXISTS movie_merge_cluster_log (
    id              BIGSERIAL PRIMARY KEY,
    run_id          TEXT NOT NULL,
    movie_id        TEXT NOT NULL,
    members         JSONB NOT NULL,              -- [asin...]
    cluster_size    INTEGER NOT NULL,
    rejected_edges  JSONB NOT NULL DEFAULT '[]', -- 链一致性拒边
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cluster_run ON movie_merge_cluster_log (run_id);
