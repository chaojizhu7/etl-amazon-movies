# -*- coding: utf-8 -*-
"""Streaming stats for movies.txt.gz (task 1): reviews, distinct ASIN, distinct
userId, reviewTime min/max. Line-by-line, never loads whole file into memory.

Record boundary = a line starting with 'product/productId:'.
Other known field prefixes start their field; any other line continues the
current field value (handles multi-line review/text).

Usage (from project root):
    .venv\\Scripts\\python.exe src\\stream_review_stats.py
Writes JSON result to logs/summary/stream_stats.json (idempotent overwrite).
"""
import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone

DATA_FILE = os.path.join("data", "raw", "movies.txt.gz")
OUT_JSON = os.path.join("logs", "summary", "stream_stats.json")

OFFICIAL = {
    "reviews": 7_911_684,
    "products": 253_059,
    "users": 889_176,
    "time_range": "1997-08 .. 2012-10",
}

PREFIXES = [
    "product/productId:",
    "review/userId:",
    "review/profileName:",
    "review/helpfulness:",
    "review/score:",
    "review/time:",
    "review/summary:",
    "review/text:",
]


def flush_record(rec, stats, asins, users):
    if not rec:
        return
    stats["records_read"] += 1
    pid = rec.get("product/productId:", "").strip()
    uid = rec.get("review/userId:", "").strip()
    t_raw = rec.get("review/time:", "").strip()
    s_raw = rec.get("review/score:", "").strip()

    bad = []
    if not pid:
        bad.append("empty_product_id")
    if not t_raw or not t_raw.lstrip("-").isdigit():
        bad.append("bad_time")
    if bad:
        stats["quarantined_records"] += 1
        for b in bad:
            stats["q_reason"][b] += 1
        return

    stats["valid_records"] += 1
    asins.add(pid)
    if uid:
        users.add(uid)
    else:
        stats["empty_user_id"] += 1

    t = int(t_raw)
    if stats["time_min"] is None or t < stats["time_min"]:
        stats["time_min"] = t
    if stats["time_max"] is None or t > stats["time_max"]:
        stats["time_max"] = t

    try:
        sv = float(s_raw)
        if not (1.0 <= sv <= 5.0):
            stats["bad_score"] += 1
    except ValueError:
        stats["bad_score"] += 1


def main() -> int:
    if not os.path.isfile(DATA_FILE):
        print(f"ERROR: missing {DATA_FILE}")
        return 1

    t0 = time.time()
    stats = {
        "records_read": 0,
        "valid_records": 0,
        "quarantined_records": 0,
        "q_reason": {},
        "empty_user_id": 0,
        "bad_score": 0,
        "encoding_replaced": 0,
        "time_min": None,
        "time_max": None,
        "parser_version": "v0.1",
    }
    asins = set()
    users = set()
    rec = {}
    cur_key = None

    with gzip.open(DATA_FILE, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "\ufffd" in line:
                stats["encoding_replaced"] += 1
            stripped = line.rstrip("\n")
            matched = None
            for p in PREFIXES:
                if stripped.startswith(p):
                    matched = p
                    break
            if matched is not None:
                if matched == "product/productId:" and rec:
                    flush_record(rec, stats, asins, users)
                    rec = {}
                rec[matched] = stripped[len(matched):]
                cur_key = matched
            else:
                if cur_key is not None:
                    rec[cur_key] = rec[cur_key] + "\n" + stripped
    if rec:
        flush_record(rec, stats, asins, users)

    elapsed = time.time() - t0
    read_ok = stats["records_read"] == stats["valid_records"] + stats["quarantined_records"]

    def fmt_t(ts):
        return (
            f"{ts} ({datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC)"
            if ts is not None
            else "None"
        )

    print("==== stream_review_stats v0.1 ====")
    print(f"file                 : {DATA_FILE}")
    print(f"records_read         : {stats['records_read']:,}")
    print(f"valid_records        : {stats['valid_records']:,}")
    print(f"quarantined_records  : {stats['quarantined_records']:,}")
    print(f"check read==valid+q  : {'OK' if read_ok else 'FAIL'}")
    print("quarantine reasons:")
    for k, v in sorted(stats["q_reason"].items()):
        print(f"  {k:<18}: {v:,}")
    print(f"empty_user_id        : {stats['empty_user_id']:,}")
    print(f"bad_score(1-5)       : {stats['bad_score']:,}")
    print(f"encoding_replaced    : {stats['encoding_replaced']:,}")
    print(f"distinct_asin        : {len(asins):,}")
    print(f"distinct_user        : {len(users):,}")
    print(f"review_time_min      : {fmt_t(stats['time_min'])}")
    print(f"review_time_max      : {fmt_t(stats['time_max'])}")
    print(f"elapsed_seconds      : {elapsed:.1f}")
    print()
    print("==== official comparison ====")
    rows = [
        ("reviews", OFFICIAL["reviews"], stats["records_read"]),
        ("products (distinct ASIN)", OFFICIAL["products"], len(asins)),
        ("users (distinct userId)", OFFICIAL["users"], len(users)),
    ]
    all_match = True
    for name, off, act in rows:
        ok = off == act
        all_match = all_match and ok
        print(f"| {name:<24} | official {off:>10,} | actual {act:>10,} | {'MATCH' if ok else 'DIFF'} |")

    tmin, tmax = stats["time_min"], stats["time_max"]
    if tmin is not None:
        lo = datetime.fromtimestamp(tmin, tz=timezone.utc).strftime("%Y-%m")
        hi = datetime.fromtimestamp(tmax, tz=timezone.utc).strftime("%Y-%m")
        time_ok = lo == "1997-08" and hi == "2012-10"
        all_match = all_match and time_ok
        print(f"| {'time range':<24} | official {OFFICIAL['time_range']:>10} | actual {lo} .. {hi} | {'MATCH' if time_ok else 'DIFF'} |")
    print(f"OVERALL: {'ALL MATCH' if all_match else 'HAS DIFF - follow investigation order'}")
    if not all_match:
        print("investigation order (禁止为凑数修改结果):")
        print("  1. download integrity  (size==3,321,791,660; SHA-256 vs download_meta.json)")
        print("  2. empty user values   (see empty_user_id counter)")
        print("  3. duplicate records   (dedup pass, task 8)")
        print("  4. parsing rules       (record boundary / field prefixes / encoding)")

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    result = dict(stats)
    result.update(
        {
            "distinct_asin": len(asins),
            "distinct_user": len(users),
            "elapsed_seconds": round(elapsed, 1),
            "official": OFFICIAL,
            "overall_match": all_match,
            "record_boundary": "line starting with product/productId:",
        }
    )
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"json written: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
