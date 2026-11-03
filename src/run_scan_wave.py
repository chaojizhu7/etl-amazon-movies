# -*- coding: utf-8 -*-
"""Run ONE scan wave (task 2, scope B) then stop. Manual "继续" advances waves.

Per-wave flow: estimate time -> pick batch -> scrapy scan -> record crawl_attempt
-> update queue -> print stats + cumulative progress + next-wave count.

Scope B: movie ASINs (any tier, from data/raw/asin_movies.txt if present)
         + non-movie high tier. Priority: movies first, then high by priority.

Idempotent/resumable: picks only status='pending'; every attempt recorded
with attempt_no = prior attempts + 1; interrupted runs leave no duplicates
(ON CONFLICT DO NOTHING on (asin, run_id, attempt_no)).

Usage (from project root):
    .venv\\Scripts\\python.exe src\\run_scan_wave.py
"""
import gzip
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import psycopg

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAX_BATCH = 500
WAVE_BUDGET_S = 900          # 15 min hard budget per wave
DELAY = 1.5                  # seconds between requests (scrapy DOWNLOAD_DELAY)
AVG_FETCH_S = 0.8            # estimated per-request transfer+processing time
MOVIE_LIST = os.path.join(ROOT, "data", "raw", "asin_movies.txt")  # from task 3 (optional)
DB = dict(host="localhost", port="5432", dbname="etl_movie", user="postgres", password="postgres")

STATUS_MAP = {  # spider processing_status -> crawl_attempt.content_feature
    "success": "product_page",
    "access_restricted": "verification_page",
    "page_not_found": "not_found",
    "parse_failed": "other",
}


def feature_of(status: str) -> str:
    return STATUS_MAP.get(status, "error")


def load_movies():
    if not os.path.isfile(MOVIE_LIST):
        return []
    return [l.strip() for l in open(MOVIE_LIST, encoding="utf-8") if l.strip() and not l.startswith("#")]


def main() -> int:
    t0 = time.time()
    movies = load_movies()

    # ---- time estimate & batch sizing (reduce batch if > 15 min) ----
    est_per_req = DELAY + AVG_FETCH_S
    batch = min(MAX_BATCH, int(WAVE_BUDGET_S / est_per_req))
    eta_min = round(batch * est_per_req / 60, 1)
    print(f"scope: movies={len(movies)} (from task3 list {'present' if movies else 'absent'}) + non-movie high tier")
    print(f"estimate: {est_per_req:.1f}s/req -> batch={batch} (cap {MAX_BATCH}), ETA ~{eta_min} min (budget 15 min)")

    with psycopg.connect(**DB) as conn:
        # wave number for run_id
        prev_waves = conn.execute(
            "SELECT count(DISTINCT run_id) FROM crawl_attempt WHERE run_id LIKE 'scan_wave_%'"
        ).fetchone()[0]
        wave_no = prev_waves + 1
        run_id = f"scan_wave_{wave_no:03d}"

        # ---- pick batch (idempotent: only pending) ----
        if movies:
            rows = conn.execute(
                """
                SELECT asin FROM product_crawl_queue
                WHERE status = 'pending' AND (priority_tier = 'high' OR asin = ANY(%s))
                ORDER BY (CASE WHEN asin = ANY(%s) THEN 0 ELSE 1 END), priority
                LIMIT %s
                """,
                (movies, movies, batch),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT asin FROM product_crawl_queue
                WHERE status = 'pending' AND priority_tier = 'high'
                ORDER BY priority
                LIMIT %s
                """,
                (batch,),
            ).fetchall()
        asins = [r[0] for r in rows]

    if not asins:
        print("no pending ASINs in scope - nothing to do (scope B exhausted; see backfill plan)")
        return 0

    wave_batch = len(asins)
    eta_min = round(wave_batch * est_per_req / 60, 1)
    print(f"\nwave {run_id}: {wave_batch} ASINs, ETA ~{eta_min} min")

    # ---- write asin file + run scrapy (single wave) ----
    os.makedirs(os.path.join(ROOT, "logs", "waves"), exist_ok=True)
    asin_file = os.path.join(ROOT, "data", "raw", f"asin_{run_id}.txt")
    manifest = os.path.join(ROOT, "snapshots", f"crawl_manifest_{run_id}.jsonl")
    log_file = os.path.join(ROOT, "logs", "waves", f"{run_id}.log")
    with open(asin_file, "w", encoding="utf-8") as f:
        f.write("\n".join(asins) + "\n")

    cmd = [
        sys.executable, "-m", "scrapy", "crawl", "product",
        "-a", f"asin_file={asin_file}",
        "-s", f"DOWNLOAD_DELAY={DELAY}",
        "-s", "RANDOMIZE_DOWNLOAD_DELAY=False",
        "-s", "CONCURRENT_REQUESTS_PER_DOMAIN=1",
        "-s", "ROBOTSTXT_OBEY=True",
        "-O", manifest,
        "--logfile", log_file,
    ]
    print(f"running scrapy (log: {log_file}) ...")
    subprocess.run(cmd, cwd=os.path.join(ROOT, "src", "crawler"), check=False)

    # ---- record results: crawl_attempt + queue status ----
    items = []
    if os.path.isfile(manifest):
        items = [json.loads(l) for l in open(manifest, encoding="utf-8") if l.strip()]

    with psycopg.connect(**DB) as conn:
        for it in items:
            asin = it["asin"]
            status = it.get("processing_status", "fetch_failed")
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
            # queue status only moves forward from pending
            conn.execute(
                """
                UPDATE product_crawl_queue
                SET status = %s, updated_at = now()
                WHERE asin = %s AND status = 'pending'
                """,
                (status, asin),
            )
        conn.commit()

        # ---- stats ----
        wave_counts = {}
        for it in items:
            s = it.get("processing_status", "fetch_failed")
            wave_counts[s] = wave_counts.get(s, 0) + 1

        cumulative = dict(conn.execute(
            "SELECT status, count(*) FROM product_crawl_queue GROUP BY status"
        ).fetchall())
        attempted = conn.execute(
            "SELECT count(DISTINCT asin) FROM crawl_attempt"
        ).fetchone()[0]
        next_wave = conn.execute(
            "SELECT count(*) FROM product_crawl_queue WHERE status='pending' AND priority_tier='high'"
        ).fetchone()[0]
        if movies:
            next_wave += conn.execute(
                "SELECT count(*) FROM product_crawl_queue WHERE status='pending' AND asin = ANY(%s)",
                (movies,),
            ).fetchone()[0]

    elapsed = round(time.time() - t0, 1)
    print(f"\n==== wave {run_id} done in {elapsed}s ====")
    print(f"this wave ({len(items)} results):")
    for k, v in sorted(wave_counts.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    total_queue = sum(cumulative.values())
    print(f"cumulative: attempted distinct ASINs = {attempted:,} / 253,059")
    print(f"queue states: {cumulative} (sum={total_queue:,})")
    print(f"next wave pending in scope: {next_wave:,}")
    print("stopped (say '继续' to run next wave)")

    os.makedirs(os.path.join(ROOT, "logs", "summary"), exist_ok=True)
    with open(os.path.join(ROOT, "logs", "summary", f"{run_id}_stats.json"), "w", encoding="utf-8") as f:
        json.dump({
            "run_id": run_id, "wave_size": len(items), "wave_counts": wave_counts,
            "attempted_total": attempted, "queue_states": cumulative,
            "next_wave_pending": next_wave, "elapsed_seconds": elapsed,
            "finished_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, f, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
