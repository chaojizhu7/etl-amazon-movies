# -*- coding: utf-8 -*-
"""Analyze a crawl manifest: per-item status + content probe (product vs verification page).

Usage (from project root):
    .venv\\Scripts\\python.exe src\\analyze_manifest.py --manifest snapshots/crawl_manifest_batch010.jsonl
"""
import argparse
import gzip
import json
import os
import sys
from collections import Counter

CAPTCHA_MARKERS = ["characters you see", "automated access", "Robot Check", "validateCaptcha", "api-services-support"]
PRODUCT_MARKERS = ["productTitle", 'id="dp"', "dp-container", '"ASIN"']


def probe(snapshot_path):
    if not snapshot_path or not os.path.isfile(snapshot_path):
        return "no_snapshot", 0
    cand = snapshot_path
    if not os.path.isfile(cand):
        cand = os.path.join("snapshots", "pages", os.path.basename(snapshot_path))
    if not os.path.isfile(cand):
        return "no_snapshot", 0
    with gzip.open(cand, "rb") as g:
        raw = g.read()
    text = raw[:200_000].decode("utf-8", errors="replace")
    if any(m in text for m in CAPTCHA_MARKERS):
        return "verification_page(captcha)", len(raw)
    if any(m in text for m in PRODUCT_MARKERS):
        return "product_page", len(raw)
    return "unknown_content", len(raw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args()

    items = [json.loads(l) for l in open(args.manifest, encoding="utf-8") if l.strip()]
    counts = Counter(it.get("processing_status") for it in items)
    print(f"manifest: {args.manifest}  total={len(items)}")
    print("status distribution:")
    for k, v in counts.most_common():
        print(f"  {k}: {v}")
    print()
    print("| asin | http_status | content_length | processing_status | content_probe | raw_size |")
    print("|------|-------------|----------------|-------------------|---------------|----------|")
    for it in items:
        kind, size = probe(it.get("snapshot_path"))
        print(
            f"| {it['asin']} | {it.get('http_status')} | {it.get('content_length')} "
            f"| {it.get('processing_status')} | {kind} | {size} |"
        )
    restricted = counts.get("access_restricted", 0)
    success = counts.get("success", 0)
    print()
    print(f"DECISION INPUT: access_restricted={restricted} (stop if >=8)  success={success} (continue if >=5)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
