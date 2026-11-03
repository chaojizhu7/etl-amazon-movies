# -*- coding: utf-8 -*-
"""Record a crawl manifest into crawl_attempt + product_crawl_queue. Replay-safe.

Usage (from project root):
    .venv\\Scripts\\python.exe src\\record_manifest.py --manifest snapshots/crawl_manifest_scan_wave_001.jsonl --run-id scan_wave_001
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import psycopg

DB = dict(host="localhost", port="5432", dbname="etl_movie", user="postgres", password="postgres")
STATUS_MAP = {
    "success": "product_page",
    "access_restricted": "verification_page",
    "page_not_found": "not_found",
    "parse_failed": "other",
}


def feature_of(status: str) -> str:
    return STATUS_MAP.get(status, "error")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()

    items = [json.loads(l) for l in open(args.manifest, encoding="utf-8") if l.strip()]
    run_id = args.run_id
    wave_counts = {}

    with psycopg.connect(**DB) as conn:
        for it in items:
            asin = it["asin"]
            status = it.get("processing_status", "fetch_failed")
            wave_counts[status] = wave_counts.get(status, 0) + 1
            attempt_no = conn.execute(
                "SELECT count(*) FROM crawl_attempt WHERE asin = %s", (asin,)
            ).fetchone()[0] + 1
            conn.execute(
                """
                INSERT INTO crawl_attempt
                    (asin, run_id, attempt_no, http_status, response_bytes,
                     content_feature, evidence_path, content_sha256)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (asin, run_id, attempt_no) DO NOTHING
                """,
                (asin, run_id, attempt_no, it.get("http_status"), it.get("content_length"),
                 feature_of(status), it.get("snapshot_path"), it.get("content_sha256")),
            )
            conn.execute(
                """
                UPDATE product_crawl_queue
                SET status = %s, updated_at = now()
                WHERE asin = %s AND status = 'pending'
                """,
                (status, asin),
            )
        conn.commit()

        cumulative = dict(conn.execute(
            "SELECT status, count(*) FROM product_crawl_queue GROUP BY status"
        ).fetchall())
        attempted = conn.execute("SELECT count(DISTINCT asin) FROM crawl_attempt").fetchone()[0]
        next_high = conn.execute(
            "SELECT count(*) FROM product_crawl_queue WHERE status='pending' AND priority_tier='high'"
        ).fetchone()[0]

    total_queue = sum(cumulative.values())
    print(f"==== recorded {len(items)} results from {args.manifest} as {run_id} ====")
    print("this wave:")
    for k, v in sorted(wave_counts.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print(f"cumulative: attempted distinct ASINs = {attempted:,} / 253,059")
    print(f"queue states: {cumulative} (sum={total_queue:,})")
    print(f"next wave pending (high tier): {next_high:,}")
    print(f"recorded_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")

    os.makedirs(os.path.join("logs", "summary"), exist_ok=True)
    with open(os.path.join("logs", "summary", f"{run_id}_stats.json"), "w", encoding="utf-8") as f:
        json.dump({
            "run_id": run_id, "wave_size": len(items), "wave_counts": wave_counts,
            "attempted_total": attempted, "queue_states": cumulative,
            "next_wave_pending_high": next_high,
            "recorded_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, f, indent=2, ensure_ascii=False)
    print(f"stats written: logs/summary/{run_id}_stats.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
