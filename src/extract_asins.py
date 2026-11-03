# -*- coding: utf-8 -*-
"""Extract first N distinct ASINs from movies.txt.gz for trial crawl. Idempotent.

Usage (from project root):
    .venv\\Scripts\\python.exe src\\extract_asins.py --n 20 --out data/raw/asin_trial20.txt
"""
import argparse
import gzip
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out", default=os.path.join("data", "raw", "asin_trial20.txt"))
    ap.add_argument("--src", default=os.path.join("data", "raw", "movies.txt.gz"))
    args = ap.parse_args()

    seen = []
    seen_set = set()
    with gzip.open(args.src, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("product/productId:"):
                asin = line.split(":", 1)[1].strip()
                if asin and asin not in seen_set:
                    seen_set.add(asin)
                    seen.append(asin)
                    if len(seen) >= args.n:
                        break

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(seen) + "\n")
    print(f"extracted {len(seen)} distinct ASINs -> {args.out}")
    for a in seen:
        print(f"  {a}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
