# -*- coding: utf-8 -*-
"""Verify SHA-256 of data/raw/movies.txt.gz against data/raw/download_meta.json.

Exit code 0 = match, 1 = mismatch or missing file, 2 = bad meta.
Idempotent: safe to re-run any number of times.
Expected usage (from project root):
    py -3 scripts/verify_meta.py
"""
import hashlib
import json
import os
import sys

DATA_FILE = os.path.join("data", "raw", "movies.txt.gz")
META_FILE = os.path.join("data", "raw", "download_meta.json")


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if not os.path.isfile(META_FILE):
        print(f"ERROR: meta file missing: {META_FILE}")
        return 2
    if not os.path.isfile(DATA_FILE):
        print(f"ERROR: data file missing: {DATA_FILE}")
        return 1

    with open(META_FILE, "r", encoding="utf-8") as f:
        meta = json.load(f)

    expected = meta.get("sha256", "")
    expected_size = meta.get("file_size_bytes", -1)
    if not expected:
        print("ERROR: meta has no sha256 field")
        return 2

    actual_size = os.path.getsize(DATA_FILE)
    print(f"Expected size : {expected_size}")
    print(f"Actual size   : {actual_size}")
    print(f"Expected SHA  : {expected}")
    print("Computing actual SHA-256...")
    actual = sha256_of(DATA_FILE)
    print(f"Actual SHA    : {actual}")

    if actual == expected and actual_size == expected_size:
        print("RESULT: MATCH")
        return 0
    print("RESULT: MISMATCH")
    return 1


if __name__ == "__main__":
    sys.exit(main())
