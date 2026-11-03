# -*- coding: utf-8 -*-
"""Self-test: run record_meta.py + verify_meta.py against a tiny dummy .gz.

Proves the scripts are runnable and idempotent before the real 3.3 GB download.
Expected usage (from project root):
    py -3 scripts/selftest_meta.py
"""
import gzip
import hashlib
import json
import os
import subprocess
import sys

DUMMY = os.path.join("data", "raw", "movies.txt.gz")
DUMMY_BODY = b"product/productId: TEST0001\nreview/userId: ATEST\nreview/text: hello etl\n"


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def main() -> int:
    os.makedirs(os.path.dirname(DUMMY), exist_ok=True)
    with gzip.open(DUMMY, "wb") as f:
        f.write(DUMMY_BODY)
    print(f"dummy created: {DUMMY} ({os.path.getsize(DUMMY)} bytes)")

    py = sys.executable
    r1 = subprocess.run([py, os.path.join("scripts", "record_meta.py")], capture_output=True, text=True)
    print("--- record_meta.py run1 ---")
    print(r1.stdout)
    assert r1.returncode == 0, r1.stderr

    with open(os.path.join("data", "raw", "download_meta.json"), "r", encoding="utf-8") as f:
        meta1 = json.load(f)
    assert meta1["file_size_bytes"] == os.path.getsize(DUMMY)
    assert meta1["sha256"] == sha256_of(DUMMY)
    assert meta1["parser_version"] == "v0.1"

    # idempotent second run
    r2 = subprocess.run([py, os.path.join("scripts", "record_meta.py")], capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr
    with open(os.path.join("data", "raw", "download_meta.json"), "r", encoding="utf-8") as f:
        meta2 = json.load(f)
    assert meta2["sha256"] == meta1["sha256"], "idempotent re-run changed sha256!"

    v = subprocess.run([py, os.path.join("scripts", "verify_meta.py")], capture_output=True, text=True)
    print("--- verify_meta.py ---")
    print(v.stdout)
    assert v.returncode == 0, "verify should MATCH on unmodified file"

    print("SELFTEST PASS: record idempotent, verify MATCH")
    print("NOTE: this dummy just overwrote data/raw/ -- delete movies.txt.gz + download_meta.json before real download.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
