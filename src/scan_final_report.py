# -*- coding: utf-8 -*-
"""Scope-B wrap-up + QA-1 final scan report (task 2). Idempotent.

1. Mark pending non-movie medium/low ASINs as not_crawled
   (reason=non_movie_low_priority_skip) — reversible via backfill plan.
2. Terminal state distribution (mutually exclusive, sum must = 253,059).
3. Success snapshot index CSV (evidence list).
4. Restricted evidence count.
5. Coverage summary by tier x movie/non-movie; overall denominator 253,059.

Usage (from project root):
    .venv\\Scripts\\python.exe src\\scan_final_report.py
Writes: logs/summary/scan_final_stats.json, snapshots/success_snapshot_index.csv
"""
import csv
import json
import os
import sys
from datetime import datetime, timezone

import psycopg

DB = dict(host="localhost", port="5432", dbname="etl_movie", user="postgres", password="postgres")
MOVIE_LIST = os.path.join("data", "raw", "asin_movies.txt")
OUT_JSON = os.path.join("logs", "summary", "scan_final_stats.json")
OUT_CSV = os.path.join("snapshots", "success_snapshot_index.csv")
TOTAL_EXPECTED = 253_059


def main() -> int:
    movies = [l.strip() for l in open(MOVIE_LIST, encoding="utf-8") if l.strip()]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with psycopg.connect(**DB) as conn:
        # ---- 1) mark not_crawled (idempotent: only touches pending) ----
        cur = conn.execute(
            """
            UPDATE product_crawl_queue
            SET status = 'not_crawled',
                status_reason = 'non_movie_low_priority_skip',
                updated_at = now()
            WHERE status = 'pending'
              AND priority_tier IN ('medium','low')
              AND NOT (asin = ANY(%s))
            """,
            (movies,),
        )
        marked = cur.rowcount
        conn.commit()

        # ---- 2) terminal distribution ----
        dist = dict(conn.execute(
            "SELECT status, count(*) FROM product_crawl_queue GROUP BY status"
        ).fetchall())
        total = sum(dist.values())

        # ---- 3) success snapshot index (latest attempt evidence per asin) ----
        rows = conn.execute(
            """
            SELECT q.asin, q.review_count, q.priority_tier, a.evidence_path,
                   a.content_sha256, a.attempted_at
            FROM product_crawl_queue q
            JOIN LATERAL (
                SELECT evidence_path, content_sha256, attempted_at
                FROM crawl_attempt a
                WHERE a.asin = q.asin AND a.content_feature = 'product_page'
                ORDER BY a.attempted_at DESC LIMIT 1
            ) a ON true
            WHERE q.status = 'success'
            ORDER BY q.priority
            """
        ).fetchall()
        os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
        with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["asin", "review_count", "priority_tier", "snapshot_path", "content_sha256", "fetched_at_utc"])
            for r in rows:
                w.writerow(list(r))

        # ---- 4) restricted evidence ----
        restricted_total = dist.get("access_restricted", 0)
        restricted_with_evidence = conn.execute(
            "SELECT count(DISTINCT asin) FROM crawl_attempt WHERE content_feature='verification_page' AND evidence_path IS NOT NULL"
        ).fetchone()[0]

        # ---- 5) coverage matrix: tier x movie ----
        matrix = conn.execute(
            """
            SELECT priority_tier,
                   CASE WHEN asin = ANY(%s) THEN 'movie' ELSE 'non_movie' END AS grp,
                   status, count(*)
            FROM product_crawl_queue
            GROUP BY 1, 2, 3
            """,
            (movies,),
        ).fetchall()

    groups = {}
    for tier, grp, status, cnt in matrix:
        g = groups.setdefault((tier, grp), {"total": 0})
        g[status] = g.get(status, 0) + cnt
        g["total"] += cnt

    print("==== QA-1 terminal distribution ====")
    print(f"not_crawled marked this run: {marked:,}")
    for k in ["success", "access_restricted", "not_crawled", "fetch_failed", "parse_failed", "pending", "page_not_found", "timeout"]:
        if k in dist:
            print(f"  {k}: {dist[k]:,}")
    print(f"  TOTAL: {total:,}  check=={TOTAL_EXPECTED}: {total == TOTAL_EXPECTED}")
    print(f"\nsuccess snapshots indexed: {len(rows):,} -> {OUT_CSV}")
    print(f"restricted evidence: {restricted_total:,} (distinct ASINs with verification snapshot: {restricted_with_evidence:,})")

    print("\n==== coverage matrix (tier x movie) ====")
    print(f"{'tier':<8} {'group':<10} {'total':>8} {'success':>8} {'restricted':>11} {'not_crawled':>12} {'other':>8} {'succ%':>7} {'restr%':>8} {'skip%':>8}")
    for (tier, grp) in sorted(groups.keys()):
        g = groups[(tier, grp)]
        t = g["total"]
        s, r, n = g.get("success", 0), g.get("access_restricted", 0), g.get("not_crawled", 0)
        other = t - s - r - n
        print(f"{tier:<8} {grp:<10} {t:>8,} {s:>8,} {r:>11,} {n:>12,} {other:>8,} {100*s/t:>6.1f}% {100*r/t:>7.1f}% {100*n/t:>7.1f}%")

    print(f"\n==== overall rates (denominator = {TOTAL_EXPECTED:,}) ====")
    for label, k in [("success", "success"), ("access_restricted", "access_restricted"), ("not_crawled", "not_crawled")]:
        v = dist.get(k, 0)
        print(f"  {label}: {v:,} = {100*v/TOTAL_EXPECTED:.2f}%")

    result = {
        "generated_at_utc": now,
        "not_crawled_marked": marked,
        "terminal_distribution": dist,
        "total": total,
        "total_matches_expected": total == TOTAL_EXPECTED,
        "success_snapshots_indexed": len(rows),
        "restricted_total": restricted_total,
        "restricted_with_evidence": restricted_with_evidence,
        "coverage_matrix": {f"{t}|{g}": v for (t, g), v in groups.items()},
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\njson written: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
