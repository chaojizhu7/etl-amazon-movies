# -*- coding: utf-8 -*-
"""Compute SHA-256 for IMDb dataset files; write checksums.sha256 + download_meta.json.

Idempotent: re-running overwrites both files with freshly measured values.
Usage (from project root):
    .venv\\Scripts\\python.exe src\\imdb_checksums.py
"""
import hashlib
import json
import os
import sys
import time

IMDB_DIR = os.path.join("data", "raw", "imdb")
CHECKSUMS = os.path.join(IMDB_DIR, "checksums.sha256")
META = os.path.join(IMDB_DIR, "download_meta.json")
BASE_URL = "https://datasets.imdbws.com/"

FILES = [
    "title.basics.tsv.gz",
    "title.crew.tsv.gz",
    "name.basics.tsv.gz",
    "title.principals.tsv.gz",
    "title.ratings.tsv.gz",
]


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    entries = []
    lines = []
    for fname in FILES:
        path = os.path.join(IMDB_DIR, fname)
        if not os.path.isfile(path):
            print(f"ERROR: missing {path}")
            return 1
        st = os.stat(path)
        t0 = time.time()
        digest = sha256_of(path)
        elapsed = time.time() - t0
        print(f"{fname}: {st.st_size:,} bytes  sha256={digest}  ({elapsed:.1f}s)")
        lines.append(f"{digest}  {fname}")
        entries.append(
            {
                "file_name": fname,
                "download_url": BASE_URL + fname,
                "file_size_bytes": st.st_size,
                "sha256": digest,
                "download_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
            }
        )

    with open(CHECKSUMS, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"written: {CHECKSUMS}")

    meta = {
        "dataset": "IMDb Non-Commercial Datasets",
        "source_page": "https://data.imdb.com/non-commercial-datasets/",
        "batch_date": "2026-11-03",
        "license": "IMDb non-commercial terms; course coursework use only; do not redistribute raw files",
        "files": entries,
        "meta_recorded_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(META, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"written: {META}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
