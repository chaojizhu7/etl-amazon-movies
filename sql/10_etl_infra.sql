-- 任务ETL：运行登记与质量事件（失败处置/中断恢复/幂等依据）
-- 幂等：CREATE IF NOT EXISTS

-- ETL 运行登记（task 级状态机：支持"成功不重做、失败单独补跑"）
CREATE TABLE IF NOT EXISTS etl_run_task (
    run_id           TEXT NOT NULL,
    task_id          TEXT NOT NULL,             -- extract/crawl/classify/merge/enrich/dedup/lineage/quality_check
    state            TEXT NOT NULL              -- running / success / failed / skipped
        CHECK (state IN ('running','success','failed','skipped')),
    try_number       INTEGER NOT NULL DEFAULT 1,
    started_at       TIMESTAMPTZ,
    ended_at         TIMESTAMPTZ,
    rule_versions    JSONB NOT NULL DEFAULT '{}',  -- 各规则版本参数
    input_ref        JSONB NOT NULL DEFAULT '{}',
    output_ref       JSONB NOT NULL DEFAULT '{}',
    error            TEXT,
    PRIMARY KEY (run_id, task_id, try_number)
);
CREATE INDEX IF NOT EXISTS idx_ert_state ON etl_run_task (run_id, state);

-- 质量事件（失败/校验违例，on_failure 告警落库）
CREATE TABLE IF NOT EXISTS quality_issue (
    id               BIGSERIAL PRIMARY KEY,
    run_id           TEXT NOT NULL,
    task_id          TEXT,
    issue_type       TEXT NOT NULL               -- task_failed / check_violation / fk_orphan / count_mismatch
        CHECK (issue_type IN ('task_failed','check_violation','fk_orphan','count_mismatch')),
    detail           TEXT NOT NULL,
    severity         TEXT NOT NULL DEFAULT 'error'
        CHECK (severity IN ('info','warning','error')),
    status           TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','resolved')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_qi_run ON quality_issue (run_id, status);

-- 迁移：解除对 movie_id/person_id 的硬外键（重算重建允许暂态悬挂；
-- 一致性由 quality_check 的 fk_orphan 检查保证，避免重跑被 FK 锁死）
ALTER TABLE release_facts DROP CONSTRAINT IF EXISTS release_facts_movie_id_fkey;
ALTER TABLE field_assertion DROP CONSTRAINT IF EXISTS field_assertion_movie_id_fkey;
ALTER TABLE movie_person DROP CONSTRAINT IF EXISTS movie_person_movie_id_fkey;
ALTER TABLE movie_person DROP CONSTRAINT IF EXISTS movie_person_person_id_fkey;
ALTER TABLE product_movie DROP CONSTRAINT IF EXISTS product_movie_movie_id_fkey;
ALTER TABLE movie_alias DROP CONSTRAINT IF EXISTS movie_alias_movie_id_fkey;


-- 人工清噪锚：movie_manual_split（传递吸收噪声防回摆，node_a=须拆出的噪声成员）
CREATE TABLE IF NOT EXISTS movie_manual_split (
    node_a      TEXT NOT NULL,
    node_b      TEXT NOT NULL,
    reason      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (node_a, node_b)
);
