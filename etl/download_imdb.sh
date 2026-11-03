#!/bin/bash
# Download IMDb non-commercial datasets (core tables) with resume. Idempotent.
set -e
DIR="/mnt/c/Users/28746/Desktop/etl-amazon/data/raw/imdb"
mkdir -p "$DIR"
cd "$DIR"
BASE="https://datasets.imdbws.com"
# order: small -> large
FILES="title.ratings.tsv.gz title.crew.tsv.gz title.basics.tsv.gz name.basics.tsv.gz title.principals.tsv.gz"
for f in $FILES; do
  if [ -f "$f" ] && [ -s "$f" ]; then
    echo "exists: $f ($(stat -c%s "$f") bytes) - verify size vs remote later"
    continue
  fi
  echo "== downloading $f =="
  curl.exe -L -C - --retry 5 --retry-delay 10 -o "$f" "$BASE/$f"
  echo "done: $f ($(stat -c%s "$f") bytes)"
done
echo "== summary =="
ls -la "$DIR"
