-- 人工合并组锚定表（修复产物；merge 重跑重放依据，防 id 空间碰撞）
CREATE TABLE IF NOT EXISTS movie_manual_group (
    canonical_movie_id TEXT NOT NULL,
    asin               TEXT NOT NULL,
    PRIMARY KEY (canonical_movie_id, asin)
);
CREATE INDEX IF NOT EXISTS idx_mmg_asin ON movie_manual_group (asin);
