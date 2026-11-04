-- 任务7 字段级来源冲突选择（rules/field_selection_rules_v1.md v1.1 语义见规则 v1.0）
-- 幂等：CREATE IF NOT EXISTS + upsert（run_id 追加；候选行永不删除）
-- 执行：src/select_fields.py

CREATE TABLE IF NOT EXISTS field_assertion (
    id               BIGSERIAL PRIMARY KEY,
    movie_id         TEXT NOT NULL REFERENCES movie_entity(movie_id),
    field_name       TEXT NOT NULL
        CHECK (field_name IN ('title','release_year','genres','directors','starring')),
    value_raw        TEXT,                      -- 原文保留
    value_norm       TEXT NOT NULL,             -- 规范值（标题 canon / YYYY / 类型名 / 人名 canon）
    value_role       TEXT NOT NULL              -- canonical / alias / work_genre / seller_category_tag
        CHECK (value_role IN ('canonical','alias','work_genre','seller_category_tag',
                              'work_crew','page_credit','work_cast','candidate_year')),
    source           TEXT NOT NULL
        CHECK (source IN ('imdb','product_page','manual','system')),
    source_locator   TEXT,
    extracted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    match_rule       TEXT NOT NULL,
    confidence       NUMERIC(3,2),
    status           TEXT NOT NULL
        CHECK (status IN ('candidate','selected','rejected','conflict')),
    decision_basis   TEXT
        CHECK (decision_basis IS NULL OR decision_basis IN ('rule_auto','manual_inherit')),
    reject_reason    TEXT
        CHECK (reject_reason IS NULL OR reject_reason IN
               ('semantic_out_of_scope','superseded_by_higher_authority',
                'contradicts_verified_value','duplicate_candidate','role_mismatch')),
    rules_version    TEXT NOT NULL,
    run_id           TEXT NOT NULL,
    UNIQUE (movie_id, field_name, value_norm, value_role, source, run_id)
);
CREATE INDEX IF NOT EXISTS idx_fa_field ON field_assertion (field_name, status);
CREATE INDEX IF NOT EXISTS idx_fa_movie ON field_assertion (movie_id);
