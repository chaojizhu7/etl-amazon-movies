# -*- coding: utf-8 -*-
"""Build product_crawl_queue from movies.txt.gz (task 2, step 1). Idempotent.

- Streams reviews, counts reviews per ASIN
- Applies rules/crawl_priority_v0.1.md (sort by review_count desc, asin asc)
- Upserts into PostgreSQL product_crawl_queue (preserves status/created_at on rerun)
- Writes batch plan (top 100) + asin list file

Usage (from project root):
    .venv\\Scripts\\python.exe src\\build_crawl_queue.py
"""
import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone

import psycopg

DATA_FILE = os.path.join("data", "raw", "movies.txt.gz")
SQL_FILE = os.path.join("sql", "01_crawl_queue.sql")
BATCH_FILE = os.path.join("data", "raw", "asin_batch_001.txt")
PLAN_FILE = os.path.join("etl", "batch_001_plan.md")
STATS_JSON = os.path.join("logs", "summary", "crawl_queue_stats.json")
RULES_VERSION = "v0.1"
BATCH_SIZE = 100

DB = dict(host="localhost", port="5432", dbname="etl_movie", user="postgres", password="postgres")


def tier(count: int) -> str:
    if count > 100:
        return "high"
    if count >= 10:
        return "medium"
    return "low"


def main() -> int:
    t0 = time.time()
    if not os.path.isfile(DATA_FILE):
        print(f"ERROR: missing {DATA_FILE}")
        return 1

    # 1) count reviews per ASIN (streaming)
    print("streaming movies.txt.gz to count reviews per ASIN ...")
    counts = {}
    total = 0
    with gzip.open(DATA_FILE, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("product/productId:"):
                asin = line.split(":", 1)[1].strip()
                if asin:
                    counts[asin] = counts.get(asin, 0) + 1
                    total += 1
    print(f"  reviews={total:,} distinct_asin={len(counts):,}")

    # 2) rank per rules/crawl_priority_v0.1.md
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    rows = [
        (asin, cnt, idx, tier(cnt), f"https://www.amazon.com/dp/{asin}")
        for idx, (asin, cnt) in enumerate(ranked, start=1)
    ]

    # 3) DDL + upsert (idempotent: keeps status/created_at on rerun)
    with open(SQL_FILE, encoding="utf-8") as f:
        ddl = f.read()
    with psycopg.connect(**DB) as conn:
        conn.execute(ddl)
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO product_crawl_queue
                    (asin, review_count, priority, priority_tier, candidate_url)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (asin) DO UPDATE SET
                    review_count  = EXCLUDED.review_count,
                    priority      = EXCLUDED.priority,
                    priority_tier = EXCLUDED.priority_tier,
                    candidate_url = EXCLUDED.candidate_url,
                    updated_at    = now()
                """,
                rows,
            )
        conn.commit()

        # 4) stats
        stats = dict(
            rules_version=RULES_VERSION,
            built_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            source_reviews=total,
            distinct_asin=len(counts),
            expected_asin=253_059,
            count_match=(len(counts) == 253_059),
            elapsed_seconds=round(time.time() - t0, 1),
        )
        for label, sql in [
            ("total", "SELECT count(*) FROM product_crawl_queue"),
            ("tier_high", "SELECT count(*) FROM product_crawl_queue WHERE priority_tier='high'"),
            ("tier_medium", "SELECT count(*) FROM product_crawl_queue WHERE priority_tier='medium'"),
            ("tier_low", "SELECT count(*) FROM product_crawl_queue WHERE priority_tier='low'"),
            ("status_pending", "SELECT count(*) FROM product_crawl_queue WHERE status='pending'"),
            ("max_reviews", "SELECT max(review_count) FROM product_crawl_queue"),
            ("min_reviews", "SELECT min(review_count) FROM product_crawl_queue"),
        ]:
            stats[label] = conn.execute(sql).fetchone()[0]

        # 5) batch 1 = top 100 by priority
        batch = conn.execute(
            "SELECT asin, review_count, priority, priority_tier, candidate_url "
            "FROM product_crawl_queue ORDER BY priority LIMIT %s",
            (BATCH_SIZE,),
        ).fetchall()

    os.makedirs(os.path.dirname(BATCH_FILE), exist_ok=True)
    with open(BATCH_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(r[0] for r in batch) + "\n")

    # 6) plan doc
    tier_counts = {"high": 0, "medium": 0, "low": 0}
    for r in batch:
        tier_counts[r[3]] += 1
    delay = 30  # seconds, per 数据源策略.md wave config
    eta_min = round((BATCH_SIZE - 1) * delay / 60 + BATCH_SIZE * 2 / 60, 1)
    lines = [
        "# 第一批采集计划 batch_001",
        "",
        f"- 生成时间：{stats['built_at_utc']}",
        f"- 规则版本：crawl_priority {RULES_VERSION}（review_count 降序 + asin 升序破并列）",
        f"- 范围：priority 1–{BATCH_SIZE}（Top-Review 前 100）",
        f"- 分层分布：high {tier_counts['high']} / medium {tier_counts['medium']} / low {tier_counts['low']}",
        f"- 请求间隔：{delay}s ± 10s 抖动（数据源策略 1.1 波内参数）",
        f"- **预计耗时：约 {eta_min} 分钟**（99 间隔×{delay}s + 100 次抓取×~2s，不含重试）",
        f"- ASIN 清单文件：`{BATCH_FILE}`（spider -a asin_file=... 直接可读）",
        "",
        "## 队列统计（全量）",
        "",
        f"- 总数：{stats['total']:,}（目标 253,059 → {'✅ 一致' if stats['count_match'] else '❌ 不一致'}）",
        f"- 分层：high(>100) {stats['tier_high']:,} / medium(10-100) {stats['tier_medium']:,} / low(<10) {stats['tier_low']:,}",
        f"- 评论数范围：min {stats['min_reviews']} / max {stats['max_reviews']:,}",
        "",
        "## 批次清单（按 priority 升序）",
        "",
        "| priority | asin | review_count | tier |",
        "|----------|------|--------------|------|",
    ]
    for asin, cnt, pri, tr, url in batch:
        lines.append(f"| {pri} | {asin} | {cnt} | {tr} |")
    lines += [
        "",
        "## 执行命令（确认后运行）",
        "",
        "```powershell",
        "cd C:\\Users\\28746\\Desktop\\etl-amazon\\src\\crawler",
        "..\\..\\.venv\\Scripts\\python.exe -m scrapy crawl product \\",
        "  -a asin_file=../../data/raw/asin_batch_001.txt \\",
        "  -s DOWNLOAD_DELAY=30 -s CONCURRENT_REQUESTS_PER_DOMAIN=1 \\",
        "  -O ../../snapshots/crawl_manifest_batch001.jsonl \\",
        "  --logfile ../../logs/crawl_batch001.log",
        "```",
    ]
    os.makedirs(os.path.dirname(PLAN_FILE), exist_ok=True)
    with open(PLAN_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    with open(STATS_JSON, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print(json.dumps(stats, indent=2, ensure_ascii=False))
    print(f"batch list: {BATCH_FILE} ({len(batch)} ASINs)")
    print(f"plan doc  : {PLAN_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
