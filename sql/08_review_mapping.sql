-- 任务8 评论清洗与重复识别（去重目标声明见 rules/输出报告）
-- 幂等：CREATE IF NOT EXISTS + run_id 全量重建 staging/mapping
-- 执行：src/clean_reviews.py

-- 原始记录索引（staging：7,911,684 行全量，原始记录永远保留在 movies.txt.gz）
CREATE TABLE IF NOT EXISTS review_record_index (
    asin            TEXT NOT NULL,
    record_ordinal  INTEGER NOT NULL,           -- movies.txt 记录序号（1-based，原始定位）
    user_id         TEXT,
    review_time     BIGINT,
    score           NUMERIC,
    helpful_num     INTEGER,
    helpful_den     INTEGER,
    h5_key          TEXT NOT NULL,              -- hash(ASIN+用户+时间+评分+文本) 候选键
    hfull_key       TEXT NOT NULL,              -- hash(全 8 字段) 逐字符一致才同
    h_utex          TEXT NOT NULL,              -- hash(用户+文本) 跨版本识别
    PRIMARY KEY (asin, record_ordinal)
);
CREATE INDEX IF NOT EXISTS idx_rri_h5 ON review_record_index (h5_key);
CREATE INDEX IF NOT EXISTS idx_rri_hf ON review_record_index (hfull_key);
CREATE INDEX IF NOT EXISTS idx_rri_utex ON review_record_index (h_utex);

-- 逻辑评论 ↔ 原始记录映射
CREATE TABLE IF NOT EXISTS review_mapping (
    logical_id      TEXT NOT NULL,              -- = hfull_key（完全一致记录共享）
    asin            TEXT NOT NULL,
    record_ordinal  INTEGER NOT NULL,
    map_status      TEXT NOT NULL               -- unique / primary / duplicate_copy
        CHECK (map_status IN ('unique','primary','duplicate_copy')),
    run_id          TEXT NOT NULL,
    PRIMARY KEY (asin, record_ordinal)
);
CREATE INDEX IF NOT EXISTS idx_rm_logical ON review_mapping (logical_id);

-- 清洗异常
CREATE TABLE IF NOT EXISTS review_anomaly (
    id              BIGSERIAL PRIMARY KEY,
    asin            TEXT NOT NULL,
    record_ordinal  INTEGER NOT NULL,
    anomaly_type    TEXT NOT NULL
        CHECK (anomaly_type IN ('helpfulness_num_gt_den','helpfulness_negative',
                                'helpfulness_unparseable','bad_score','empty_user')),
    raw_value       TEXT,
    run_id          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ra_run ON review_anomaly (run_id);
