-- product_crawl_queue: 任务2 产品页面采集队列
-- 优先级规则见 rules/crawl_priority_v0.1.md
-- Idempotent: CREATE IF NOT EXISTS + upsert 语义（见 src/build_crawl_queue.py）

CREATE TABLE IF NOT EXISTS product_crawl_queue (
    asin            TEXT PRIMARY KEY,
    review_count    INTEGER NOT NULL,
    priority        INTEGER NOT NULL,            -- 1 = 最高优先级
    priority_tier   TEXT NOT NULL                -- high (>100) / medium (10-100) / low (<10)
        CHECK (priority_tier IN ('high','medium','low')),
    candidate_url   TEXT NOT NULL,               -- https://www.amazon.com/dp/<ASIN>
    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','crawling','success','page_not_found',
                          'access_restricted','timeout','parse_failed',
                          'fetch_failed','permanently_unreachable')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_crawl_queue_priority ON product_crawl_queue (priority);
CREATE INDEX IF NOT EXISTS idx_crawl_queue_status ON product_crawl_queue (status, priority);
CREATE INDEX IF NOT EXISTS idx_crawl_queue_tier ON product_crawl_queue (priority_tier);
