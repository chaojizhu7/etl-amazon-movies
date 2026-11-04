-- 任务9 字段级数据血缘（OpenLineage 扩展到字段级）
-- 幂等：CREATE IF NOT EXISTS + run_id 全量重建
-- 加载：src/build_lineage.py  查询：lineage/query_0[1-4]_*.sql

-- 1) 源记录（文件/快照/第三方数据集/人工复核）
CREATE TABLE IF NOT EXISTS lineage_source_record (
    source_record_id   TEXT PRIMARY KEY,        -- sr_file_movies_txt / sr_page_<asin> / sr_imdb_* / sr_manual_review / sr_derived_system
    source_type        TEXT NOT NULL
        CHECK (source_type IN ('review_file','product_page_snapshot','imdb_dataset','manual_review','derived_system')),
    source_address     TEXT NOT NULL,           -- 文件路径/URL
    snapshot_sha256    TEXT,                    -- 快照/文件哈希
    raw_locator        TEXT,                    -- asin / tconst / 记录序号区间 / 文档章节
    acquired_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2) 字段级断言（统一层：每个候选值一行，未采用也保留）
CREATE TABLE IF NOT EXISTS lineage_field_assertion (
    assertion_uid      BIGSERIAL PRIMARY KEY,
    entity_type        TEXT NOT NULL CHECK (entity_type IN ('movie','edition','person')),
    entity_id          TEXT NOT NULL,
    field_name         TEXT NOT NULL,
    value_role         TEXT,                    -- canonical/alias/work_genre/seller_category_tag/...
    raw_value          TEXT,                    -- 原文
    normalized_value   TEXT,
    source             TEXT NOT NULL CHECK (source IN ('imdb','product_page','review','manual','system')),
    source_record_id   TEXT REFERENCES lineage_source_record(source_record_id),
    field_locator      TEXT,                    -- tconst/nconst/asin/记录序号
    extraction_rule    TEXT NOT NULL,
    rule_version       TEXT NOT NULL,
    extracted_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    confidence         NUMERIC(3,2),
    status             TEXT NOT NULL
        CHECK (status IN ('candidate','selected','rejected','conflict','verified','inferred','missing'))
);
CREATE INDEX IF NOT EXISTS idx_lfa_entity ON lineage_field_assertion (entity_type, entity_id, field_name);
CREATE INDEX IF NOT EXISTS idx_lfa_source ON lineage_field_assertion (source_record_id);

-- 3) 最终值（可解释：selected_assertion_uids 或候选集合+理由）
CREATE TABLE IF NOT EXISTS lineage_field_final (
    entity_type           TEXT NOT NULL,
    entity_id             TEXT NOT NULL,
    field_name            TEXT NOT NULL,
    selected_assertion_uids JSONB NOT NULL DEFAULT '[]',
    decision_reason       TEXT NOT NULL,
    rules_version         TEXT NOT NULL,
    run_id                TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id, field_name)
);

-- 4) 处理链（父子关系/操作/rule_id/run_id/时间/置信度）
CREATE TABLE IF NOT EXISTS lineage_process_step (
    step_id            BIGSERIAL PRIMARY KEY,
    parent_step_id     BIGINT REFERENCES lineage_process_step(step_id),
    op_type            TEXT NOT NULL
        CHECK (op_type IN ('extract','normalize','match','select','merge','manual_confirm','reject')),
    target_kind        TEXT NOT NULL CHECK (target_kind IN ('field','entity_pair','person_pair')),
    target_ref         TEXT NOT NULL,
    rule_id            TEXT NOT NULL,
    run_id             TEXT NOT NULL,
    executed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    confidence         NUMERIC(3,2),
    note               TEXT
);
CREATE INDEX IF NOT EXISTS idx_lps_target ON lineage_process_step (target_ref);
