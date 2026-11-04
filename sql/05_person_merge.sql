-- 任务5 人物姓名归一与消歧落库（person_merge_rules_v1.md v1.1）
-- 幂等：CREATE IF NOT EXISTS + 追加式决策（run_id 区分批次）+ 派生表全量重建
-- 执行：src/merge_persons.py --execute

CREATE TABLE IF NOT EXISTS person_entity (
    person_id        TEXT PRIMARY KEY,          -- 系统生成 p_<6位序号>（不用姓名/nconst 充当）
    name_canonical   TEXT NOT NULL,
    external_ids     JSONB NOT NULL DEFAULT '{}', -- {"imdb": ["nm..."]}
    primary_occupation TEXT,
    era_span         JSONB NOT NULL DEFAULT '{}', -- {"start":Y,"end":Y,"basis":"lifespan|work_years","inferred":bool,"confidence":n}
    status           TEXT NOT NULL DEFAULT 'merged'
        CHECK (status IN ('merged','singleton','pending_review','unresolved')),
    rules_version    TEXT NOT NULL,
    run_id           TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS person_alias (
    id               BIGSERIAL PRIMARY KEY,
    person_id        TEXT NOT NULL REFERENCES person_entity(person_id),
    name_raw         TEXT NOT NULL,              -- 原文保留不改意
    name_canon       TEXT NOT NULL,
    lang_script      TEXT,                       -- 可选：语言/文字标注
    source           TEXT NOT NULL               -- imdb / product_page / manual / review
        CHECK (source IN ('imdb','product_page','manual','review')),
    source_locator   TEXT,                       -- nconst / asin+快照哈希 / 复核标记
    run_id           TEXT NOT NULL,
    UNIQUE (person_id, name_canon, source, source_locator)
);
CREATE INDEX IF NOT EXISTS idx_palias_canon ON person_alias (name_canon);
CREATE INDEX IF NOT EXISTS idx_palias_person ON person_alias (person_id);

CREATE TABLE IF NOT EXISTS person_merge_candidate (
    pair_id          BIGSERIAL PRIMARY KEY,
    node_a           TEXT NOT NULL,              -- nconst 或 pg:<canon>:<movie_id>
    node_b           TEXT NOT NULL,
    signals          JSONB NOT NULL,
    title_coeff      NUMERIC(3,2),               -- 姓名系数 T
    score_v          NUMERIC(4,3),
    score_conf       NUMERIC(4,3),
    known_signals    INTEGER,
    rules_version    TEXT NOT NULL,
    run_id           TEXT NOT NULL,
    UNIQUE (node_a, node_b, run_id)
);

CREATE TABLE IF NOT EXISTS person_merge_decision (
    id               BIGSERIAL PRIMARY KEY,
    node_a           TEXT NOT NULL,
    node_b           TEXT NOT NULL,
    decision         TEXT NOT NULL
        CHECK (decision IN ('auto_merge','manual_merge','manual_review','no_merge')),
    reason           TEXT NOT NULL,
    rules_version    TEXT NOT NULL,
    run_id           TEXT NOT NULL,
    decided_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pdecision_pair ON person_merge_decision (node_a, node_b);
CREATE INDEX IF NOT EXISTS idx_pdecision_run ON person_merge_decision (run_id);

CREATE TABLE IF NOT EXISTS movie_person (
    id               BIGSERIAL PRIMARY KEY,
    movie_id         TEXT NOT NULL REFERENCES movie_entity(movie_id),
    person_id        TEXT NOT NULL REFERENCES person_entity(person_id),
    role             TEXT NOT NULL               -- director / actor / actress
        CHECK (role IN ('director','actor','actress')),
    ordering         INTEGER,                   -- IMDb principals ordering
    nconst           TEXT,                      -- 来源定位（imdb）
    source           TEXT NOT NULL               -- imdb / product_page
        CHECK (source IN ('imdb','product_page')),
    source_locator   TEXT,
    evidence         JSONB NOT NULL DEFAULT '{}',
    rules_version    TEXT NOT NULL,
    run_id           TEXT NOT NULL,
    UNIQUE (movie_id, person_id, role, nconst, source)
);
CREATE INDEX IF NOT EXISTS idx_mp_person ON movie_person (person_id);
CREATE INDEX IF NOT EXISTS idx_mp_movie ON movie_person (movie_id);

CREATE TABLE IF NOT EXISTS person_cluster_log (
    id               BIGSERIAL PRIMARY KEY,
    run_id           TEXT NOT NULL,
    person_id        TEXT NOT NULL,
    members          JSONB NOT NULL,
    cluster_size     INTEGER NOT NULL,
    rejected_edges   JSONB NOT NULL DEFAULT '[]',
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pcluster_run ON person_cluster_log (run_id);
