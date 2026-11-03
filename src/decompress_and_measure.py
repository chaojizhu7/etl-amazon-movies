# -*- coding: utf-8 -*-
"""Decompress data/raw/movies.txt.gz and report disk-space numbers.

Reports:
  - compressed size (bytes on disk of .gz)
  - uncompressed size (bytes on disk of resulting movies.txt, measured after decompress)
  - gzip ISIZE footer value and possible true sizes (mod 2^32 wrap analysis)
  - free space on target drive before/after

Idempotent: if movies.txt already exists and its size matches ISIZE-derived candidates,
it will NOT re-decompress (delete movies.txt first to force).

Expected usage (from project root):
    py -3 scripts/decompress_and_measure.py
"""
import gzip
import os
import shutil
import struct
import sys

GZ_FILE = os.path.join("data", "raw", "movies.txt.gz")
TXT_FILE = os.path.join("data", "raw", "movies.txt")


def fmt(n: int) -> str:
    return f"{n:,} bytes ({n / 1024**3:.2f} GiB)"


def read_isize(path: str) -> int:
    """gzip ISIZE = last 4 bytes, little-endian uint32 = uncompressed size mod 2^32."""
    with open(path, "rb") as f:
        f.seek(-4, os.SEEK_END)
        raw = f.read(4)
    return struct.unpack("<I", raw)[0]


def free_space(path: str) -> int:
    return shutil.disk_usage(os.path.dirname(os.path.abspath(path))).free


def main() -> int:
    if not os.path.isfile(GZ_FILE):
        print(f"ERROR: missing {GZ_FILE}")
        return 1

    gz_size = os.path.getsize(GZ_FILE)
    isize = read_isize(GZ_FILE)
    mod = 2 ** 32

    print(f"Compressed file : {GZ_FILE}")
    print(f"Compressed size : {fmt(gz_size)}")
    print(f"gzip ISIZE raw  : {isize:,} (= uncompressed mod 2^32)")
    print("Possible true uncompressed sizes (k = wrap count):")
    for k in range(0, 4):
        cand = isize + k * mod
        ratio = gz_size / cand if cand else 0
        print(f"  k={k}: {fmt(cand)}  (compressed/uncompressed = {ratio:.3f})")

    free_before = free_space(GZ_FILE)
    print(f"Free space on target drive: {fmt(free_before)}")

    if os.path.isfile(TXT_FILE):
        txt_size = os.path.getsize(TXT_FILE)
        print(f"\n{TXT_FILE} already exists: {fmt(txt_size)} -- skip decompress (idempotent)")
    else:
        print(f"\nDecompressing {GZ_FILE} -> {TXT_FILE} ...")
        with gzip.open(GZ_FILE, "rb") as fin, open(TXT_FILE, "wb") as fout:
            shutil.copyfileobj(fin, fout, length=16 * 1024 * 1024)
        txt_size = os.path.getsize(TXT_FILE)
        print(f"Decompressed size (ACTUAL): {fmt(txt_size)}")

    free_after = free_space(GZ_FILE)
    used_by_txt = txt_size
    print(f"\n==== Disk space summary ====")
    print(f"compressed (.gz)      : {fmt(gz_size)}")
    print(f"uncompressed (.txt)   : {fmt(txt_size)}")
    print(f"total occupied (both) : {fmt(gz_size + txt_size)}")
    print(f"free space now        : {fmt(free_after)}")
    print(f"NOTE: keep .gz as raw-layer evidence; delete .txt only if policy allows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
