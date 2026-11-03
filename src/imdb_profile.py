# -*- coding: utf-8 -*-
"""Profile downloaded IMDb tables: header check + row counts + field coverage stats.
Streaming, never loads whole files into memory. Idempotent.

Usage (from project root):
    .venv\\Scripts\\python.exe src\\imdb_profile.py
Writes logs/summary/imdb_profile.json
"""
import gzip
import json
import os
import sys
import time

IMDB_DIR = os.path.join("data", "raw", "imdb")
OUT_JSON = os.path.join("logs", "summary", "imdb_profile.json")


def profile_basics(path):
    """title.basics: total, by titleType, startYear coverage."""
    total = 0
    by_type = {}
    with_year = 0
    movies = 0
    movies_with_year = 0
    with_genre = 0
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        header = f.readline().rstrip("\n").split("\t")
        for line in f:
            total += 1
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 9:
                continue
            tconst, title_type, _, _, _, sy, _, _, genres = parts[:9]
            by_type[title_type] = by_type.get(title_type, 0) + 1
            if sy != "\\N":
                with_year += 1
            if genres != "\\N":
                with_genre += 1
            if title_type == "movie":
                movies += 1
                if sy != "\\N":
                    movies_with_year += 1
    return {
        "header": header,
        "rows": total,
        "rows_with_startYear": with_year,
        "rows_with_genres": with_genre,
        "type_movie_rows": movies,
        "type_movie_with_startYear": movies_with_year,
        "top_types": dict(sorted(by_type.items(), key=lambda x: -x[1])[:6]),
    }


def profile_simple(path, header_idx, value_idx=None, value_not_null=None):
    """Generic: rows + optional non-null value count."""
    total = 0
    non_null = 0
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        header = f.readline().rstrip("\n").split("\t")
        for line in f:
            total += 1
            if value_idx is not None:
                parts = line.rstrip("\n").split("\t")
                if len(parts) > value_idx and parts[value_idx] != value_not_null:
                    non_null += 1
    out = {"header": header, "rows": total}
    if value_idx is not None:
        out["rows_value_present"] = non_null
    return out


def profile_principals(path):
    """title.principals: rows + actor/actress/director counts."""
    total = 0
    cats = {}
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        header = f.readline().rstrip("\n").split("\t")
        for line in f:
            total += 1
            parts = line.rstrip("\n").split("\t")
            if len(parts) > 3:
                cat = parts[3]
                cats[cat] = cats.get(cat, 0) + 1
    return {"header": header, "rows": total, "category_counts": dict(sorted(cats.items(), key=lambda x: -x[1])[:8])}


def main() -> int:
    t0 = time.time()
    results = {}
    jobs = [
        ("title.basics.tsv.gz", "basics", lambda p: profile_basics(p)),
        ("title.crew.tsv.gz", "crew", lambda p: profile_simple(p, 0, value_idx=1, value_not_null="\\N")),
        ("name.basics.tsv.gz", "name_basics", lambda p: profile_simple(p, 0)),
        ("title.principals.tsv.gz", "principals", lambda p: profile_principals(p)),
        ("title.ratings.tsv.gz", "ratings", lambda p: profile_simple(p, 0)),
    ]
    for fname, key, fn in jobs:
        path = os.path.join(IMDB_DIR, fname)
        if not os.path.isfile(path):
            results[key] = {"error": f"missing {path}"}
            print(f"{key}: MISSING")
            continue
        size = os.path.getsize(path)
        t1 = time.time()
        results[key] = fn(path)
        results[key]["file_bytes"] = size
        results[key]["profile_seconds"] = round(time.time() - t1, 1)
        print(f"{key}: {results[key].get('rows', '?'):,} rows in {results[key]['profile_seconds']}s")
        if key == "basics":
            print(f"  movies={results[key]['type_movie_rows']:,} (with year {results[key]['type_movie_with_startYear']:,})")
        if key == "crew":
            print(f"  with directors={results[key].get('rows_value_present', '?'):,}")
        if key == "principals":
            print(f"  categories={results[key]['category_counts']}")

    results["total_profile_seconds"] = round(time.time() - t0, 1)
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"json written: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
