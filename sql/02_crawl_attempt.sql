-- crawl_attempt: 每次采集尝试的证据链（任务2 修正方案核心）
-- 保证"每个产品至少尝试过一次"可核验；配合 product_crawl_queue 终态统计
-- Idempotent: 与 sql/01_crawl_queue.sql 一起执行

CREATE TABLE IF NOT EXISTS crawl_attempt (
    id              BIGSERIAL PRIMARY KEY,
    asin            TEXT NOT NULL,
    run_id          TEXT NOT NULL,               -- 批次/波次标识（含规则版本）
    attempt_no      INTEGER NOT NULL,            -- 第几次尝试（1=首次）
    http_status     INTEGER,
    response_bytes  INTEGER,
    content_feature TEXT                         -- product_page / verification_page / not_found / error / other
        CHECK (content_feature IN ('product_page','verification_page','not_found','error','other')),
    evidence_path   TEXT,                        -- 快照路径（success/restricted 必有；其他可空）
    content_sha256  TEXT,                        -- 原始响应内容哈希
    attempted_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_attempt_asin ON crawl_attempt (asin, attempted_at);
CREATE INDEX IF NOT EXISTS idx_attempt_run ON crawl_attempt (run_id);
