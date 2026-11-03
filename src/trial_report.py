# -*- coding: utf-8 -*-
"""Build trial-crawl status distribution + snapshot index from manifest.
Verifies N random snapshots by recomputing SHA-256. Idempotent.

Usage (from project root):
    .venv\\Scripts\\python.exe src\\trial_report.py --manifest snapshots/crawl_manifest_trial.jsonl --verify 2
"""
import argparse
import gzip
import hashlib
import json
import os
import random
import sys
from collections import Counter

STATUS_CN = {
    "success": "成功",
    "page_not_found": "页面不存在",
    "access_restricted": "访问受限",
    "timeout": "超时",
    "parse_failed": "解析失败",
    "fetch_failed": "抓取失败",
    "http_error": "HTTP错误",
    "not_crawled": "未采集",
}


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=os.path.join("snapshots", "crawl_manifest_trial.jsonl"))
    ap.add_argument("--index", default=os.path.join("snapshots", "index_trial.csv"))
    ap.add_argument("--verify", type=int, default=2)
    args = ap.parse_args()

    items = []
    with open(args.manifest, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))

    total = len(items)
    counts = Counter(it.get("processing_status", "not_crawled") for it in items)
    print("==== trial status distribution ====")
    print(f"total ASINs crawled: {total}")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        cn = STATUS_CN.get(k, k)
        print(f"  {cn} ({k}): {v}")
    print(f"check sum == total: {sum(counts.values()) == total}")

    os.makedirs(os.path.dirname(args.index), exist_ok=True)
    cols = [
        "asin", "requested_url", "final_url", "http_status", "processing_status",
        "content_sha256", "content_length", "snapshot_path", "fetched_at_utc", "attempts",
    ]
    with open(args.index, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(cols) + "\n")
        for it in items:
            row = [str(it.get(c, "")).replace('"', "'") for c in cols]
            f.write(",".join(row) + "\n")
    print(f"index written: {args.index} ({total} rows)")

    print(f"\n==== snapshot verify (random {args.verify}, seed=42) ====")
    rng = random.Random(42)
    snap_items = [it for it in items if it.get("snapshot_path") and it.get("content_sha256")]
    sample = rng.sample(snap_items, min(args.verify, len(snap_items)))
    all_ok = True
    for it in sample:
        path = it["snapshot_path"]
        if not os.path.isfile(path):
            # normalize legacy relative paths (written from src/crawler cwd)
            cand = os.path.join("snapshots", "pages", os.path.basename(path))
            if os.path.isfile(cand):
                path = cand
        expected = it["content_sha256"]
        ok_exists = os.path.isfile(path)
        if not ok_exists:
            print(f"  {it['asin']}: MISSING {path}")
            all_ok = False
            continue
        with gzip.open(path, "rb") as g:
            raw = g.read()
        actual = hashlib.sha256(raw).hexdigest()
        ok = actual == expected
        all_ok = all_ok and ok
        print(f"  {it['asin']}: expected={expected[:16]}... actual={actual[:16]}... -> {'MATCH' if ok else 'MISMATCH'}")
        print(f"    file={path} size={os.path.getsize(path)} bytes, raw={len(raw)} bytes")
    print(f"VERIFY RESULT: {'ALL MATCH' if all_ok else 'HAS MISMATCH'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
