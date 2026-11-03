# -*- coding: utf-8 -*-
"""Peek at movies.txt.gz format: first records + field prefixes. Read-only."""
import gzip

path = r"C:\Users\28746\Desktop\etl-amazon\data\raw\movies.txt.gz"
with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
    for i, line in enumerate(f):
        if i >= 40:
            break
        print(f"{i:3d}| {line.rstrip()[:150]}")
