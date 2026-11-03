# -*- coding: utf-8 -*-
"""Record download metadata for movies.txt.gz into data/raw/download_meta.json.

Idempotent: re-running overwrites the JSON with freshly measured values.
Expected usage (from project root):
    py -3 scripts/record_meta.py
"""
import hashlib
import json
import os
import sys
import time

# ---- configuration (edit here if paths change) ----
DOWNLOAD_URL = "https://snap.stanford.edu/data/movies.txt.gz"
PARSER_VERSION = "v0.1"
DATA_FILE = os.path.join("data", "raw", "movies.txt.gz")
META_FILE = os.path.join("data", "raw", "download_meta.json")


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if not os.path.isfile(DATA_FILE):
        print(f"ERROR: file not found: {DATA_FILE}")
        return 1

    st = os.stat(DATA_FILE)
    print(f"Computing SHA-256 of {DATA_FILE} ({st.st_size} bytes)...")
    t0 = time.time()
    digest = sha256_of(DATA_FILE)
    elapsed = time.time() - t0

    meta = {
        "download_url": DOWNLOAD_URL,
        "download_time_local": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(st.st_mtime)),
        "download_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
        "file_name": os.path.basename(DATA_FILE),
        "file_size_bytes": st.st_size,
        "sha256": digest,
        "parser_version": PARSER_VERSION,
        "meta_recorded_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sha256_compute_seconds": round(elapsed, 2),
    }

    os.makedirs(os.path.dirname(META_FILE), exist_ok=True)
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"SHA-256: {digest}")
    print(f"Written: {META_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
