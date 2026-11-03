# -*- coding: utf-8 -*-
"""Task 3: movie-page screening / product classification (rules/movie_classification_v0.1.md).

Idempotent classifier over the whole product_crawl_queue:
- page evidence from success snapshots (structured fields only; no full-page keyword scan)
- IMDb exact-title candidates from data/raw/imdb/title.basics.tsv.gz (cached lookup)
- deterministic rule engine (R0-R2 hard bans, P0-P8 precedence)
- loads sql/03_product_classification.sql, upserts product_classification
  + product_movie_candidate (collection candidates, ProductMovie reserved)
- writes logs/summary/classification_stats.json + data/raw/asin_movies.txt

Usage (from project root):
    .venv\\Scripts\\python.exe src\\classify_products.py [--run-id run_20261103-01]
"""
import argparse
import glob
import gzip
import html as html_mod
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

import psycopg

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PAGES_DIR = os.path.join(ROOT, "snapshots", "pages")
IMDB_TITLES = os.path.join(ROOT, "data", "raw", "imdb", "title.basics.tsv.gz")
IMDB_CACHE = os.path.join(ROOT, "logs", "summary", "imdb_title_cache.json")
DDL_FILE = os.path.join(ROOT, "sql", "03_product_classification.sql")
OUT_STATS = os.path.join(ROOT, "logs", "summary", "classification_stats.json")
OUT_SAMPLES = os.path.join(ROOT, "logs", "summary", "classification_samples.json")
OUT_MOVIES = os.path.join(ROOT, "data", "raw", "asin_movies.txt")

RULES_VERSION = "v0.1"
DB = dict(host="localhost", port="5432", dbname="etl_movie", user="postgres", password="postgres")

CLASSES = ("movie", "non_movie", "collection", "tv_series", "concert", "other", "pending_review")
CLASS_FROM_TITLETYPE = {"movie": "movie", "tvmovie": "movie", "tvseries": "tv_series",
                        "tvminiseries": "tv_series", "tvepisode": "tv_series",
                        "tvpilot": "tv_series", "videogame": "non_movie", "short": "other"}
# video / tvSpecial / tvShort -> B-signal vote or pending (rules §5)

CATEGORY_WORDS = {
    "action & adventure", "kids & family", "science fiction & fantasy", "blu-ray movie",
    "dvd movie", "comedy", "drama", "musical", "animation", "anime & manga",
    "mystery & thrillers", "westerns", "military & war", "horror", "romance",
    "documentary", "classics", "fantasy", "music videos & concerts", "concerts & music videos",
    "fitness", "comedy/stand-up", "drama/love & romance", "exercise & fitness",
    "sports", "special interest", "education", "science & nature", "faith & spirituality",
    "teens", "holiday", "latino", "urban", "music", "tv", "movies", "boxed sets",
    "science fiction", "adventure", "war", "epics", "thrillers", "mystery", "suspense",
}

DEPARTMENTS = {"movies & tv", "music", "books", "video games", "software", "prime video",
               "video", "magazine subscriptions", "apps & games", "audible books & originals",
               "digital music", "cds & vinyl"}

VIDEO_HINT_RE = re.compile(r"\b(?:eng(?:lish)?\s+subs?(?:titled)?|subtitles?|dubbed|season\s*\d+|episodes?)\b", re.I)

# ---- title normalization (rules §4; strip table) ----
YEAR_BRACKET_RE = re.compile(r"\s*\[((?:19|20)\d{2})\]\s*")
EDITION_BRACKET_RE = re.compile(
    r"\s*\[[^\[\]]*(?:Blu-?ray|DVD|VHS|HD\s*DVD|4K|Ultra\s*HD|UHD|\bBD\b|Widescreen|Full[- ]?Screen"
    r"|NTSC|PAL|Region|Import|Collector|Special|Anniversary|Limited|Unrated|Uncut|Extended"
    r"|Restored|Remastered|Digitally|\bUMD\b|\bPSP\b|VideoCD|\bVCD\b)[^\[\]]*\]\s*", re.I)
EDITION_PAREN_RE = re.compile(
    r"\s*\([^()]*(?:Edition|Blu-?ray|DVD|VHS|4K|Ultra\s*HD|\bBD\b|Widescreen|Full[- ]?Screen"
    r"|\bDisc\b|Packaging|Gift Set|Steelbook|\bCase\b|Snow Globe|Bookmarks|Charm|Necklace"
    r"|Bonus|Subtitle|Subs\b|NTSC|Region|Import|Toy\b|Pack\b)[^()]*\)\s*", re.I)
FORMAT_TAIL_RE = re.compile(r"\s*[-–—:]\s*(?:BD|DVD|Blu-?ray|VHS|HD\s*DVD|4K)\s*$", re.I)
SEASON_RE = re.compile(r"[-:,\s]*\bSeason\s+(\d+)(?:\s*[-–]\s*(\d+))?\b", re.I)
COMPLETE_SERIES_RE = re.compile(
    r"[:,\s]*(?:The\s+)?Complete\s+(?:\w+\s+)?(?:Series|Seasons|Collection)\b[:,\s]*", re.I)
AKA_RE = re.compile(r"\baka\s+([^()\[\]]+)", re.I)
PHRASE_STRIP = [
    re.compile(p, re.I) for p in [
        r"\b\d{1,2}(?:st|nd|rd|th)\s+Anniversary(?:\s+Edition)?\b",
        r"\b(?:Ultimate|Special|Deluxe|Limited|Extended|Uncut|Unrated|Remastered|Restored)"
        r"\s+(?:Collector'?s?\s+)?(?:Edition|Cut|Version)\b",
        r"\bCollector'?s?\s+Edition\b",
        r"\b(?:Two|Three|Four|\d+)\s*[- ]?\s*Discs?(?:\s*(?:Set|Pack))?\b",
        r"\b\d+[- ]?pack\b",
        r"\bGift\s+Set\b",
        r"\b(?:Special|Collectors?|Anniversary|Limited|Ultimate|Extended|Uncut|Widescreen"
        r"|Full[- ]?Screen|Fullscreen|Steelbook|Unrated|Remastered|Restored)\s+Edition\b",
        r"\bSteelbook(?:\s+Case)?\b",
        r"\bwith\s+(?:Snow Globe|Bookmarks|Charm Necklace)(?:\s+and\s+(?:Toy|Bonus Features))?\b",
        r"\b(?:and\s+)?Bonus\s+Features\b",
        r"\bYear\s+(?:19|20)\d{2}\b",
        r"\b(?:Blu-?ray|DVD|VHS|HD\s*DVD|4K|Ultra\s*HD|\bBD\b)\b",
        r"\bEng(?:lish)?\s+Subs?(?:titled)?\b",
        r"\b(?:English|Spanish|French|German|Italian|Japanese|Chinese|Korean)\s+Sub(?:titles?|s)\b",
        r"\bSubtitles?\b",
        r"\bRequires?\s+a\s+Multi[- ]?Region\s+Player\b",
        r"\s*:\s*The\s+Movie\b",
        r"\b(?:Full[- ]?Screen|Fullscreen|Widescreen|Letterbox)\b",
        r"\bUMD(?:\s+for\s+PSP)?(?:\s+Pack)?\b",
        r"\s+(?:Ultimate|Special|Deluxe|Collectors?)\s*$",
    ]
]
LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.I)


def canon(title: str) -> str:
    """Canonical form for exact matching: NFKD, & -> and, separators->space,
    letter/digit boundary, comma-inversion, no leading article."""
    t = html_mod.unescape(title or "")
    t = unicodedata.normalize("NFKD", t)
    t = t.replace("&", " and ")
    t = re.sub(r"[-–—:/|,]", " ", t)
    t = re.sub(r"\bvol\b\.?", "volume", t, flags=re.I)
    t = re.sub(r"\bpt\b\.?", "part", t, flags=re.I)
    t = re.sub(r"([a-zà-ÿ])(\d)", r"\1 \2", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip().strip(" .,;-")
    t = re.sub(r"\b(?:the|a|an)(?:\s+|$)", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    return t.casefold()


def parse_runtime(text):
    """'2 hours and 6 minutes' -> 126; '95 minutes' -> 95; else None."""
    if not text:
        return None
    m = re.search(r"(\d+)\s*hours?\s*(?:and\s*(\d+)\s*minutes?)?", text, re.I)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2) or 0)
        return h * 60 + mi
    m = re.search(r"(\d+)\s*minutes?", text, re.I)
    return int(m.group(1)) if m else None


# ---------------- page evidence ----------------
def _visible_lines(html):
    h = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    h = re.sub(r"<style[\s\S]*?</style>", " ", h, flags=re.I)
    txt = re.sub(r"<[^>]+>", "\n", h)
    return [re.sub(r"\s+", " ", l).strip() for l in txt.splitlines() if re.sub(r"\s+", " ", l).strip()]


def _field_after(lines, label, span=2):
    for i, l in enumerate(lines):
        if l.rstrip(":") == label:
            for j in range(i + 1, min(i + 1 + span, len(lines))):
                v = lines[j]
                if v and v not in ("&mdash;", "See more", "See More"):
                    return v
    return None


def extract_page_evidence(html):
    ev = {}
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    title_tag = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    ev["title_tag_raw"] = title_tag
    # department (B5): tail ': Movies & TV' or ' - Amazon.com Music'
    dept = None
    if " - Amazon.com" in title_tag:
        dept = title_tag.split(" - Amazon.com", 1)[1].strip(" .:-").lower()
    elif title_tag.startswith("Amazon.com:"):
        tail = html_mod.unescape(title_tag.rsplit(":", 1)[-1]).strip(" .:-").lower()
        if tail in DEPARTMENTS:
            dept = tail
        elif re.search(r"movies|music|books|games|software|prime video", tail, re.I):
            dept = tail
    ev["department"] = dept

    # cast/crew names from <title> tail: "Amazon.com: X : N1, N2, ... : Movies & TV"
    t = title_tag
    if " - Amazon.com" in t:
        t = t.split(" - Amazon.com", 1)[0]
    else:
        t = t.rsplit(":", 1)[0]
    parts = [p.strip() for p in t.split(" : ")]
    name_chunk = parts[-1] if len(parts) >= 2 else ""
    ev["title_names"] = [n.strip() for n in name_chunk.split(",") if n.strip()]

    m = re.search(r'id="productTitle"[^>]*>(.*?)</span>', html, re.S) or \
        re.search(r'id="productTitle"[^>]*>(.*?)</', html, re.S)
    ev["product_title"] = re.sub(r"\s+", " ", html_mod.unescape(m.group(1))).strip() if m else ""

    ev["breadcrumbs"] = [html_mod.unescape(c) for c in re.findall(
        r'<li><span class="a-list-item"><a[^>]*>([^<]+)</a></span></li>', html)]
    lines = _visible_lines(html)
    ev["rating"] = html_mod.unescape(_field_after(lines, "Rated") or _field_after(lines, "MPAA rating") or "")
    ev["genre"] = html_mod.unescape(_field_after(lines, "Genre") or "")
    ev["format"] = html_mod.unescape(_field_after(lines, "Format", 3) or "")
    ev["language"] = html_mod.unescape(_field_after(lines, "Language") or "")
    ev["discs"] = _field_after(lines, "Number Of Discs") or _field_after(lines, "Number of discs") or ""
    ev["runtime_text"] = _field_after(lines, "Runtime") or _field_after(lines, "Run time") or ""
    ev["runtime_min"] = parse_runtime(ev["runtime_text"])
    roles = []
    for i, l in enumerate(lines):
        m = re.match(r"^\((Actor|Director|Writer|Producer|Creator|Screenwriter)\)[,;]?$", l)
        if m and i > 0:
            roles.append({"name": lines[i - 1], "role": m.group(1)})
    ev["contributors"] = roles[:15]
    # description region only (NOT full page): feature bullets / productDescription / synopsis
    desc_bits = []
    for pat in [r'id="feature-bullets"[^>]*>([\s\S]{0,4000}?)</div>',
                r'"productDescription"[^>]*>([\s\S]{0,3000}?)</div>',
                r'movieSynopsis[^>]*>([\s\S]{0,2000}?)</div>']:
        m = re.search(pat, html, re.I)
        if m:
            seg = re.sub(r"<[^>]+>", " ", m.group(1))
            desc_bits.append(re.sub(r"\s+", " ", seg).strip())
    ev["description"] = " ".join(desc_bits)[:1500]
    return ev


# ---------------- title normalization / markers ----------------
def normalize_title(raw):
    """Return dict: query_variants, alias_candidates, markers (season/collection/concert/other)."""
    out = {"raw": raw, "markers": {}}
    t = html_mod.unescape(raw or "")
    t = re.sub(r"\s+", " ", t).strip()

    m = SEASON_RE.search(t)
    if m:
        out["markers"]["season"] = int(m.group(1))
    if re.search(r"\bComplete\s+(?:Series|Seasons)\b", t, re.I):
        out["markers"]["complete_series"] = True
    if re.search(r"\bComplete\s+(?:\w+\s+)?Collection\b", t, re.I):
        out["markers"]["complete_collection"] = True
    if re.search(r"\bEpisodes?\b", t, re.I):
        out["markers"]["episodes"] = True
    if re.search(r"\b(?:Trilogy|Quadrilogy|Anthology)\b|\b\d+\s*[- ]\s*(?:Movie|Film|Feature)s?\b"
                 r"|\b(?:Movie|Film)s?\s+(?:Collection|Box\s?Set)\b|\bBoxed?\s+Set\b"
                 r"|\bComplete\s+(?:Movie\s+)?Collection\b", t, re.I):
        out["markers"]["multi_work_title"] = True
    if re.search(r"\bLive\s+(?:in|from|at)\s+[A-Z]|In\s+Concert\b|Live\s+Concert\b|Concert\s+Tour\b",
                 t, re.I):
        out["markers"]["concert_title"] = True
    if re.search(r"\b(?:Workout|Fitness|Yoga|Exercise|Pilates|Zumba|Training|Instructional)\b", t, re.I):
        out["markers"]["fitness_title"] = True
    if re.search(r"\b(?:Stand-?Up|Comedy\s+Special)\b", t, re.I):
        out["markers"]["standup_title"] = True
    if re.search(r"\bKaraoke\b", t, re.I):
        out["markers"]["karaoke_title"] = True

    ym = YEAR_BRACKET_RE.search(t)
    if ym:
        out["markers"]["year_hint"] = int(ym.group(1))
    t2 = YEAR_BRACKET_RE.sub(" ", t)

    aliases = []
    for m in AKA_RE.finditer(t2):
        al = re.sub(r"\s+", " ", m.group(1)).strip(" .,:;-")
        if al:
            aliases.append(al)

    t3 = EDITION_BRACKET_RE.sub(" ", t2)
    # parentheticals: keep content as alias candidate, strip as edition
    def _paren(m):
        inner = m.group(1).strip()
        if inner and not EDITION_PAREN_RE.match(f" ({inner})"):
            aliases.append(inner)
        return " "
    t3 = re.sub(r"\(([^()]*)\)", _paren, t3)
    t3 = EDITION_PAREN_RE.sub(" ", t3)
    t3 = SEASON_RE.sub(" ", t3)
    t3 = COMPLETE_SERIES_RE.sub(" ", t3)
    t3 = FORMAT_TAIL_RE.sub(" ", t3)
    for p in PHRASE_STRIP:
        t3 = p.sub(" ", t3)

    t3 = re.sub(r"\s+", " ", t3).strip(" .,:;-")
    out["base"] = t3

    variants = []
    if t3:
        # 尾缀 "Complete" 视为版本词（如 Advent Children Complete）直接剥离
        t3 = re.sub(r"\s+Complete\s*$", "", t3).strip(" .,:;-")
        variants.append(t3)
        no_paren = re.sub(r"\([^()]*\)", " ", html_mod.unescape(raw or ""))
        for p in PHRASE_STRIP:
            no_paren = p.sub(" ", no_paren)
        no_paren = SEASON_RE.sub(" ", COMPLETE_SERIES_RE.sub(" ", FORMAT_TAIL_RE.sub(" ", no_paren)))
        no_paren = re.sub(r"\s+Complete\s*$", "", no_paren)
        no_paren = re.sub(r"\s+", " ", no_paren).strip(" .,:;-")
        if no_paren and no_paren != t3:
            variants.append(no_paren)
    for al in aliases:
        if al not in variants:
            variants.append(al)
    # slash-separated dual titles ("Kingdom of Heaven/Crusaders")
    for v in list(variants):
        if "/" in html_mod.unescape(raw or ""):
            for part in re.split(r"[/|]", html_mod.unescape(raw or "")):
                part = re.sub(r"\([^()]*\)", " ", part)
                part = re.sub(r"\[[^\[\]]*\]", " ", part)
                part = re.sub(r"\s+", " ", part).strip(" .,:;-")
                for p in PHRASE_STRIP:
                    part = p.sub(" ", part)
                part = SEASON_RE.sub(" ", COMPLETE_SERIES_RE.sub(" ", FORMAT_TAIL_RE.sub(" ", part)))
                part = re.sub(r"\s+", " ", part).strip(" .,:;-")
                if part and part not in variants:
                    variants.append(part)
            break
    out["query_variants"] = variants
    out["alias_candidates"] = aliases
    return out


# ---------------- IMDb lookup (cached) ----------------
def load_cache():
    if os.path.isfile(IMDB_CACHE):
        try:
            with open(IMDB_CACHE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def imdb_scan(queries):
    """Stream title.basics once; return {query: [row...]} for exact canon matches."""
    by_canon = {}
    for q in queries:
        by_canon.setdefault(canon(q), []).append(q)
    hits = {q: [] for q in queries}
    with gzip.open(IMDB_TITLES, "rt", encoding="utf-8", errors="replace") as f:
        header = f.readline()
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 6:
                continue
            pt, ot = p[2], p[3]
            row = {"tconst": p[0], "titleType": p[1], "primaryTitle": pt,
                   "originalTitle": ot, "startYear": p[5],
                   "runtimeMinutes": p[6] if len(p) > 6 else "\\N",
                   "genres": p[7] if len(p) > 7 else "\\N"}
            seen_q = set()
            for cand in (pt, ot):
                for q in by_canon.get(canon(cand), []):
                    if q not in seen_q:
                        seen_q.add(q)
                        hits[q].append(row)
    return hits


def imdb_lookup(queries):
    cache = load_cache()
    missing = sorted({q for q in queries if q not in cache})
    if missing:
        print(f"  imdb: scanning title.basics for {len(missing)} queries ...")
        found = imdb_scan(missing)
        for q in missing:
            cache[q] = found.get(q, [])
        os.makedirs(os.path.dirname(IMDB_CACHE), exist_ok=True)
        with open(IMDB_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
    return {q: cache.get(q, []) for q in queries}


def resolve_imdb(variants, lookup):
    """Union candidates across variants; classify match_status + titleType set."""
    rows, seen = [], set()
    for v in variants:
        for r in lookup.get(v, []):
            if r["tconst"] not in seen:
                seen.add(r["tconst"])
                rows.append(r)
    if not rows:
        return {"status": "none", "tconsts": [], "types": [], "rows": []}
    types = sorted({r["titleType"].lower() for r in rows})
    mapped = {CLASS_FROM_TITLETYPE.get(t) for t in types}
    mapped.discard(None)
    if len(rows) == 1:
        status = "unique"
    elif len(mapped) == 1:
        status = "unique_class"
    else:
        status = "ambiguous"
    return {"status": status, "tconsts": [r["tconst"] for r in rows],
            "types": types, "rows": rows[:20], "mapped_classes": sorted(mapped)}


# ---------------- cast/year disambiguation (rules §4 演职员消歧) ----------------
CAST_CACHE = os.path.join(ROOT, "logs", "summary", "imdb_cast_cache.json")


def _name_toks(s):
    s = html_mod.unescape(s or "").lower()
    return [t for t in re.sub(r"[^a-z0-9à-ÿ ]", " ", s).split()
            if len(t) > 1 and t not in ("jr", "sr", "ii", "iii", "iv")]


def _name_match(page_toks, imdb_toks):
    if not page_toks or not imdb_toks:
        return False
    if page_toks == imdb_toks:
        return True
    if len(page_toks) == 1:
        return page_toks[0] == imdb_toks[-1]
    return page_toks[0] in imdb_toks and page_toks[-1] == imdb_toks[-1]


def ensure_cast_cache(tconsts):
    """{tconst: {cast:[names], directors:[names]}} via title.principals + title.crew + name.basics."""
    cache = {}
    if os.path.isfile(CAST_CACHE):
        try:
            with open(CAST_CACHE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    need = {t for t in tconsts if t not in cache}
    if not need:
        return cache
    print(f"  cast: scanning title.principals/crew/name.basics for {len(need)} tconsts ...")
    raw = {}
    nconsts = set()
    with gzip.open(os.path.join(ROOT, "data", "raw", "imdb", "title.principals.tsv.gz"),
                   "rt", encoding="utf-8", errors="replace") as f:
        f.readline()
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[0] not in need or len(p) < 4:
                continue
            d = raw.setdefault(p[0], {"cast": [], "dirs": []})
            if p[3] in ("actor", "actress"):
                try:
                    order = int(p[1])
                except Exception:
                    order = 99
                d["cast"].append((order, p[2]))
                nconsts.add(p[2])
            elif p[3] == "director":
                d["dirs"].append(p[2])
                nconsts.add(p[2])
    with gzip.open(os.path.join(ROOT, "data", "raw", "imdb", "title.crew.tsv.gz"),
                   "rt", encoding="utf-8", errors="replace") as f:
        f.readline()
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[0] not in need or len(p) < 2:
                continue
            d = raw.setdefault(p[0], {"cast": [], "dirs": []})
            for n in p[1].split(","):
                if n and n != "\\N":
                    d["dirs"].append(n)
                    nconsts.add(n)
    names = {}
    with gzip.open(os.path.join(ROOT, "data", "raw", "imdb", "name.basics.tsv.gz"),
                   "rt", encoding="utf-8", errors="replace") as f:
        f.readline()
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[0] in nconsts and len(p) > 1:
                names[p[0]] = p[1]
    for t, d in raw.items():
        cast = sorted({names.get(n, "") for _, n in sorted(d["cast"])[:25]} - {""})
        dirs = sorted({names.get(n, "") for n in d["dirs"][:5]} - {""})
        cache[t] = {"cast": cast, "directors": dirs}
    for t in need:
        cache.setdefault(t, {"cast": [], "directors": []})
    os.makedirs(os.path.dirname(CAST_CACHE), exist_ok=True)
    with open(CAST_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    return cache


def resolve_by_year_or_cast(imdb, page, year_hint):
    """For ambiguous candidate sets: year-tag unique match, else page-name vs IMDb cast overlap."""
    rows = imdb.get("rows") or []
    if imdb.get("status") != "ambiguous" or not rows:
        return None
    if year_hint:
        yr = [r for r in rows if str(r.get("startYear")) == str(year_hint)]
        if len(yr) == 1:
            return {"winner": yr[0], "method": "year_match",
                    "detail": f"标题年份标记 [{year_hint}] 与唯一候选 startYear 一致"}
    page_names = list(dict.fromkeys((page.get("title_names") or [])
                                    + [c["name"] for c in page.get("contributors", [])]))
    page_toks = [(n, _name_toks(n)) for n in page_names if len(_name_toks(n)) >= 1]
    if not page_toks:
        return None
    cache = ensure_cast_cache([r["tconst"] for r in rows])
    scores = []
    for r in rows:
        c = cache.get(r["tconst"], {})
        imdb_names = (c.get("directors") or []) + (c.get("cast") or [])
        imdb_toks = [(n, _name_toks(n)) for n in imdb_names]
        matched = [pn for pn, pt in page_toks if any(_name_match(pt, it) for _, it in imdb_toks)]
        scores.append((len(matched), r, matched))
    scores.sort(key=lambda x: -x[0])
    top, winner, matched = scores[0]
    second = scores[1][0] if len(scores) > 1 else 0
    if top >= 2 and top > second:
        return {"winner": winner, "method": "cast_match",
                "detail": f"页面署名与 IMDb 演职员重名 {top} 人（{', '.join(matched[:6])}），"
                          f"次优候选仅 {second} 人"}
    return None


def resolve_by_variant_unique(variants, lookup, status):
    """F2 最具体变体优先：歧义时，某规范化变体/别名单独命中 1 个非碰撞型候选 -> 采纳。"""
    if status != "ambiguous":
        return None
    singles = []
    for v in variants:
        rs = lookup.get(v) or []
        if len(rs) == 1 and rs[0]["titleType"].lower() not in ("tvepisode", "short", "tvshort"):
            singles.append(rs[0])
    if not singles:
        return None
    tset = {r["tconst"] for r in singles}
    if len(tset) == 1:
        w = singles[0]
        return {"winner": w, "method": "variant_unique_match",
                "detail": f"规范化变体/别名单独精确命中 {w['tconst']}（{w['titleType']}）"}
    return None


def resolve_by_collision_filter(imdb, page):
    """F1 同名碰撞过滤：tvEpisode/short 等无页面信号佐证时不参与类别竞争。

    Returns (filtered_rows, note) or None when filtering does not yield a single class.
    """
    rows = imdb.get("rows") or []
    if imdb.get("status") != "ambiguous" or not rows:
        return None
    mk = (page.get("norm") or {}).get("markers", {})
    genre = (page.get("genre") or "").lower()
    fmt = (page.get("format") or "").lower()
    dept = page.get("department") or ""
    plausible = {"movie", "tvmovie"}
    if mk.get("season") or mk.get("complete_series") or mk.get("episodes") or "television" in genre:
        plausible |= {"tvseries", "tvminiseries", "tvepisode", "tvpilot"}
    if mk.get("concert_title") or "concert" in genre:
        plausible |= {"video", "tvspecial"}
    if mk.get("fitness_title") or mk.get("standup_title") or mk.get("karaoke_title"):
        plausible |= {"video", "tvspecial"}
    video_carrier = any(f in fmt for f in ("dvd", "blu-ray", "blu ray", "vhs", "hd dvd", "4k")) \
        or "movies & tv" in dept
    if not video_carrier:
        plausible |= {"video", "videogame", "tvspecial"}
    filtered = [r for r in rows if r["titleType"].lower() in plausible]
    if not filtered or len(filtered) == len(rows):
        return None
    mapped = {CLASS_FROM_TITLETYPE.get(r["titleType"].lower()) for r in filtered}
    mapped.discard(None)
    if len(mapped) == 1:
        return filtered, (f"同名碰撞候选（tvEpisode/short/video 等 {len(rows) - len(filtered)} 个）"
                          f"无页面信号佐证，过滤后类别唯一：{sorted(mapped)[0]}")
    return None


# ---------------- rule engine (rules §6 P0-P8) ----------------
def classify_product(page, imdb):
    """Return dict(classification, status, confidence, pending_reason, rules_fired, rationale)."""
    fired, notes = [], []

    # --- P0 crawl state (caller passes page=None for non-success) ---
    if page is None:
        return {"classification": "pending_review", "status": "pending", "confidence": None,
                "pending_reason": None, "rules_fired": ["P0"],  # reason filled by caller
                "rationale": "无 success 快照，按 R2 不猜"}

    norm = page["norm"]
    mk = norm["markers"]
    genre = (page.get("genre") or "").lower()
    genre_items = [g.strip() for g in html_mod.unescape(page.get("genre") or "").split(",") if g.strip()]
    crumbs = " | ".join(page.get("breadcrumbs") or []).lower()
    dept = page.get("department") or ""
    runtime = page.get("runtime_min")
    directors = [c for c in page.get("contributors", []) if c["role"] == "Director"]
    actors = [c for c in page.get("contributors", []) if c["role"] == "Actor"]

    video_hint = bool(VIDEO_HINT_RE.search(norm["raw"] or ""))
    non_video_dept = dept in ("music", "books", "video games", "software") if dept else False

    # --- P1 signal conflicts ---
    if non_video_dept and video_hint and imdb["status"] == "none":
        fired.append("P1")
        return {"classification": "pending_review", "status": "pending", "confidence": None,
                "pending_reason": "signal_conflict", "rules_fired": fired,
                "rationale": f"部门信号({dept or 'unknown'})与影视格式词冲突且 IMDb 无匹配，不猜"}
    if imdb["status"] == "unique" and mk.get("multi_work_title") is None:
        tt = imdb["rows"][0]["titleType"].lower()
        if tt in ("movie", "tvmovie") and (mk.get("season") or mk.get("complete_series")):
            fired.append("P1")
            return {"classification": "pending_review", "status": "pending", "confidence": None,
                    "pending_reason": "signal_conflict", "rules_fired": fired,
                    "rationale": "IMDb 唯一匹配 movie 与页面剧集标记冲突，不猜"}

    # --- P2 non_movie ---
    if non_video_dept and imdb["status"] in ("none", "not_attempted"):
        fired.append("P2")
        return {"classification": "non_movie", "status": "inferred", "confidence": 0.5,
                "pending_reason": None, "rules_fired": fired,
                "rationale": f"部门={dept or 'unknown'} 非影视且无影视信号"}
    if imdb["status"] in ("unique", "unique_class") and imdb.get("mapped_classes") == ["non_movie"]:
        fired.append("P2")
        return {"classification": "non_movie", "status": "evidence", "confidence": 0.8,
                "pending_reason": None, "rules_fired": fired,
                "rationale": "IMDb titleType=videoGame"}

    # --- P3 collection ---
    cand_titles = []
    for g in genre_items:
        gc = canon(g)
        if gc and gc not in {canon(c) for c in CATEGORY_WORDS} and len(g) >= 4:
            cand_titles.append(g)
    multi_title = mk.get("multi_work_title")
    if multi_title and len(cand_titles) >= 2:
        fired.append("P3")
        return {"classification": "collection", "status": "evidence", "confidence": 0.85,
                "pending_reason": None, "rules_fired": fired,
                "rationale": f"标题多片标记 + Genre 列出 {len(cand_titles)} 部候选影片",
                "candidate_movie_titles": cand_titles}
    if multi_title and mk.get("season") is None:
        fired.append("P3")
        return {"classification": "collection", "status": "inferred", "confidence": 0.6,
                "pending_reason": None, "rules_fired": fired,
                "rationale": "标题多片标记（Trilogy/Collection 等），候选影片标题未逐一列出，留任务4 映射",
                "candidate_movie_titles": cand_titles}

    # --- P4 concert（B3 类目噪声大：仅 genre 不够；IMDb video/tvspecial 须为候选主体才可佐证）---
    concert_signal = bool(mk.get("concert_title"))
    if not concert_signal and ("concert" in genre or "concert" in crumbs):
        types = set(imdb.get("types") or [])
        if types and types <= {"video", "tvspecial"}:
            concert_signal = True
        elif re.search(r"concert|live (?:in|from|at) \w+", (page.get("description") or ""), re.I):
            concert_signal = True
    if concert_signal and not (imdb["status"] in ("unique", "unique_class")
                               and imdb.get("mapped_classes") == ["movie"]):
        fired.append("P4")
        conf = 0.7 if any(t in ("video", "tvspecial") for t in imdb.get("types", [])) else 0.6
        return {"classification": "concert", "status": "inferred", "confidence": conf,
                "pending_reason": None, "rules_fired": fired,
                "rationale": f"演唱会/音乐现场信号（genre={genre or '无'}, title_markers={mk}）"}

    # --- P5 tv_series ---
    if (mk.get("season") or mk.get("complete_series") or mk.get("episodes")
            or (mk.get("complete_collection") and runtime and runtime >= 600)):
        fired.append("P5")
        src = "evidence" if imdb.get("mapped_classes") in (["tv_series"],) else "inferred"
        conf = 0.9 if src == "evidence" else 0.7
        return {"classification": "tv_series", "status": src, "confidence": conf,
                "pending_reason": None, "rules_fired": fired,
                "rationale": f"页面剧集标记 {sorted(k for k in mk if k in ('season','complete_series','episodes','complete_collection'))}"
                             + (f" + IMDb {imdb['types']}" if imdb["tconsts"] else "")}
    if imdb["status"] in ("unique", "unique_class") and imdb.get("mapped_classes") == ["tv_series"]:
        fired.append("P5")
        return {"classification": "tv_series", "status": "evidence", "confidence": 0.8,
                "pending_reason": None, "rules_fired": fired,
                "rationale": f"IMDb titleType={imdb['types']}（{','.join(imdb['tconsts'][:3])}）"}

    # --- P6 movie ---
    film_genre = any(g in genre for g in ("action", "drama", "comedy", "science fiction",
                                          "animation", "kids", "western", "horror", "thriller",
                                          "fantasy", "romance", "military", "musical", "mystery",
                                          "suspense", "war", "adventure", "classics", "epics"))
    movie_votes = [bool(runtime and 70 <= runtime <= 180), bool(directors), film_genre,
                   bool(re.match(r"^(?:PG-13|PG|G|R|NC-17)", (page.get("rating") or "")))]
    n_votes = sum(movie_votes)
    if imdb["status"] in ("unique", "unique_class") and imdb.get("mapped_classes") == ["movie"]:
        fired.append("P6")
        src, conf = "evidence", 0.9 if imdb["status"] == "unique" else 0.8
        note = f"IMDb titleType={imdb['types']}（{','.join(imdb['tconsts'][:3])}）"
        if runtime and not (70 <= runtime <= 180):
            note += f"; runtime_anomaly={runtime}min"
            conf = max(0.4, conf - 0.1)
            fired.append("R-anomaly")
        return {"classification": "movie", "status": src, "confidence": conf,
                "pending_reason": None, "rules_fired": fired, "rationale": note}
    # 弱 A1（short→other 单类）：与页面正片证据矛盾时视为不可用，落 B 级投票
    weak_other = (imdb["status"] in ("unique", "unique_class")
                  and imdb.get("mapped_classes") == ["other"]
                  and any(t == "short" for t in imdb.get("types", []))
                  and runtime and runtime >= 70)
    if imdb["status"] == "ambiguous" or weak_other:
        mapped = set(imdb.get("mapped_classes") or [])
        if "movie" in mapped and n_votes >= 2 and not (mk.get("season") or mk.get("complete_series")):
            fired.append("P6")
            conf = 0.6 if n_votes >= 3 else 0.5
            note = f"IMDb 候选跨类 {imdb['types']}，页面信号投票定 movie（{n_votes}/4）"
            if runtime and not (70 <= runtime <= 180):
                note += f"; runtime_anomaly={runtime}min"
                fired.append("R-anomaly")
            return {"classification": "movie", "status": "inferred", "confidence": conf,
                    "pending_reason": None, "rules_fired": fired, "rationale": note}
        if "tv_series" in mapped and (mk.get("season") or mk.get("complete_series")
                                      or "television" in genre):
            fired.append("P5")
            return {"classification": "tv_series", "status": "inferred", "confidence": 0.6,
                    "pending_reason": None, "rules_fired": fired,
                    "rationale": f"IMDb 候选含 tv_series {imdb['types']} + 页面剧集信号"}
        if "non_movie" in mapped and non_video_dept:
            fired.append("P2")
            return {"classification": "non_movie", "status": "inferred", "confidence": 0.5,
                    "pending_reason": None, "rules_fired": fired,
                    "rationale": f"IMDb 候选含非影视 {imdb['types']} + 非影视部门"}
        if weak_other and n_votes < 2:
            fired.append("P7")
            return {"classification": "other", "status": "inferred", "confidence": 0.5,
                    "pending_reason": None, "rules_fired": fired,
                    "rationale": f"IMDb titleType={imdb['types']}（短片），页面正片证据不足"}
        if weak_other and n_votes >= 2:
            fired.append("P6")
            return {"classification": "movie", "status": "inferred", "confidence": 0.5,
                    "pending_reason": None, "rules_fired": fired,
                    "rationale": f"IMDb 短片候选与页面正片证据矛盾（votes={n_votes}/4），按页面证据定 movie（inferred）"}
        fired.append("P8")
        return {"classification": "pending_review", "status": "pending", "confidence": None,
                "pending_reason": "imdb_ambiguous" if imdb["status"] == "ambiguous" else "evidence_insufficient",
                "rules_fired": fired,
                "rationale": f"IMDb 候选跨类且页面信号不足以消歧（types={imdb['types']}, votes={n_votes}/4, "
                             f"runtime={runtime}, genre={genre or '无'}），不猜"}
    if runtime and 70 <= runtime <= 180 and directors and (film_genre or "movies" in crumbs):
        fired.append("P6")
        conf = 0.7 if re.match(r"^(?:PG-13|PG|G|R|NC-17)", (page.get("rating") or "")) else 0.5
        return {"classification": "movie", "status": "inferred", "confidence": conf,
                "pending_reason": None, "rules_fired": fired,
                "rationale": f"正片带片长 {runtime}min + Director + 类目（{genre or crumbs}），IMDb 无可用匹配"}

    # --- P7 other ---
    other_sig = ("fitness" in genre or "stand-up" in genre or "exercise" in crumbs
                 or bool(mk.get("fitness_title")) or bool(mk.get("standup_title"))
                 or bool(mk.get("karaoke_title")))
    if other_sig:
        fired.append("P7")
        return {"classification": "other", "status": "inferred", "confidence": 0.7,
                "pending_reason": None, "rules_fired": fired,
                "rationale": f"教学/健身/喜剧特辑等信号（genre={genre or '无'}, markers={mk}）"}
    if imdb["status"] in ("unique", "unique_class") and imdb.get("mapped_classes") == ["other"]:
        fired.append("P7")
        return {"classification": "other", "status": "evidence", "confidence": 0.8,
                "pending_reason": None, "rules_fired": fired,
                "rationale": f"IMDb titleType={imdb['types']}（短片等非正片）"}
    # titleType video / tvSpecial / tvShort: B 级投票（rules §5）
    if imdb["status"] in ("unique", "unique_class") and not imdb.get("mapped_classes"):
        if concert_signal:
            fired.append("P4")
            return {"classification": "concert", "status": "inferred", "confidence": 0.6,
                    "pending_reason": None, "rules_fired": fired,
                    "rationale": f"IMDb titleType={imdb['types']} + 演唱会信号"}
        if (mk.get("standup_title") or mk.get("fitness_title") or mk.get("karaoke_title")
                or "stand-up" in genre or "fitness" in genre):
            fired.append("P7")
            return {"classification": "other", "status": "inferred", "confidence": 0.6,
                    "pending_reason": None, "rules_fired": fired,
                    "rationale": f"IMDb titleType={imdb['types']} + 喜剧特辑/教学信号"}
        if runtime and 70 <= runtime <= 180 and directors:
            fired.append("P6")
            return {"classification": "movie", "status": "inferred", "confidence": 0.5,
                    "pending_reason": None, "rules_fired": fired,
                    "rationale": f"IMDb titleType={imdb['types']} + 正片带片长 {runtime}min + Director"}
        fired.append("P8")
        return {"classification": "pending_review", "status": "pending", "confidence": None,
                "pending_reason": "evidence_insufficient", "rules_fired": fired,
                "rationale": f"IMDb titleType={imdb['types']} 需 B 级信号消歧而页面信号不足，不猜"}

    # --- P8 ---
    fired.append("P8")
    return {"classification": "pending_review", "status": "pending", "confidence": None,
            "pending_reason": "evidence_insufficient" if imdb["status"] != "ambiguous" else "imdb_ambiguous",
            "rules_fired": fired,
            "rationale": f"证据不足（imdb={imdb['status']}, types={imdb['types']}, "
                         f"runtime={runtime}, genre={genre or '无'}），按 R2 不猜"}


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("run_%Y%m%d-01")
    t0 = time.time()

    with psycopg.connect(**DB) as conn:
        with open(DDL_FILE, encoding="utf-8") as f:
            conn.execute(f.read())
        conn.commit()
        queue = {r[0]: {"review_count": r[1], "priority": r[2], "tier": r[3], "status": r[4]}
                 for r in conn.execute(
                     "SELECT asin, review_count, priority, priority_tier, status "
                     "FROM product_crawl_queue")}
        # 人工复核标记跨重跑保留（不被证据重建覆盖）
        review_marks = {r[0]: r[1] for r in conn.execute(
            "SELECT asin, evidence->'manual_review' FROM product_classification "
            "WHERE evidence ? 'manual_review'")}

    print(f"queue rows: {len(queue):,}  run_id={run_id}  rules={RULES_VERSION}")

    # snapshot paths for success candidates
    snap_map = {os.path.basename(p).split(".")[0]: p
                for p in glob.glob(os.path.join(PAGES_DIR, "*.html.gz"))}
    big = {a: p for a, p in snap_map.items() if os.path.getsize(p) >= 5000}
    print(f"snapshots: total={len(snap_map)}  product-page-size={len(big)}")

    # ---- extract evidence for product-page-size snapshots ----
    pages = {}
    for asin, path in sorted(big.items()):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            html = f.read()
        ev = extract_page_evidence(html)
        ev["snapshot_path"] = path
        ev["norm"] = normalize_title(ev.get("product_title") or "")
        pages[asin] = ev

    # ---- IMDb lookup for all query variants ----
    queries = sorted({v for ev in pages.values() for v in ev["norm"]["query_variants"] if v})
    lookup = imdb_lookup(queries)
    print(f"imdb queries: {len(queries)}  cached-hit titles: {sum(1 for q in queries if lookup.get(q))}")

    # ---- classify ----
    imdb_map = {asin: resolve_imdb(ev["norm"]["query_variants"], lookup)
                for asin, ev in pages.items()}
    amb_t = [t for im in imdb_map.values() if im["status"] == "ambiguous" for t in im["tconsts"]]
    if amb_t:
        ensure_cast_cache(sorted(set(amb_t)))
    results = {}
    for asin, meta in queue.items():
        if asin in pages:
            page = pages[asin]
            imdb = imdb_map[asin]
            resolution = resolve_by_year_or_cast(
                imdb, page, page["norm"]["markers"].get("year_hint"))
            if not resolution:
                resolution = resolve_by_variant_unique(
                    page["norm"]["query_variants"], lookup, imdb.get("status"))
            if not resolution:
                coll = resolve_by_collision_filter(imdb, page)
                if coll:
                    filtered, note = coll
                    if len(filtered) == 1:
                        resolution = {"winner": filtered[0], "method": "collision_filter",
                                      "detail": note}
                    else:
                        f_types = sorted({r["titleType"].lower() for r in filtered})
                        imdb = {"status": "unique_class",
                                "tconsts": [r["tconst"] for r in filtered],
                                "types": f_types, "rows": filtered[:20],
                                "mapped_classes": sorted(
                                    {CLASS_FROM_TITLETYPE.get(t) for t in f_types} - {None}),
                                "resolution": {"method": "collision_filter", "detail": note,
                                               "tconst": None, "original_status": "ambiguous"}}
            if resolution:
                w = resolution["winner"]
                imdb = {"status": "unique", "tconsts": [w["tconst"]],
                        "types": [w["titleType"].lower()], "rows": [w],
                        "mapped_classes": sorted(
                            {CLASS_FROM_TITLETYPE.get(w["titleType"].lower())} - {None}),
                        "resolution": {"method": resolution["method"],
                                       "detail": resolution["detail"],
                                       "tconst": w["tconst"], "original_status": "ambiguous"}}
            res = classify_product(page, imdb)
            res["page"] = page
            res["imdb"] = imdb
        else:
            st = meta["status"]
            reason = {"access_restricted": "access_restricted",
                      "permanently_unreachable": "access_restricted",
                      "fetch_failed": "fetch_failed", "timeout": "fetch_failed",
                      "page_not_found": "page_unparsed", "parse_failed": "page_unparsed",
                      "success": "page_unparsed"}.get(st, "not_crawled")
            res = {"classification": "pending_review", "status": "pending", "confidence": None,
                   "pending_reason": reason, "rules_fired": ["P0"],
                   "rationale": f"采集状态={st}，无可用 success 快照，按 R2 不猜",
                   "page": None, "imdb": {"status": "not_attempted", "tconsts": [], "types": []}}
        results[asin] = res

    # ---- upsert ----
    rows = []
    for asin, res in results.items():
        page, imdb = res.get("page"), res.get("imdb")
        evidence = {
            "rules_fired": res["rules_fired"],
            "rationale": res["rationale"],
            "page_signals": None if not page else {
                "product_title": page["product_title"], "department": page["department"],
                "breadcrumbs": page["breadcrumbs"], "genre": page["genre"],
                "rating": page["rating"], "runtime_min": page["runtime_min"],
                "discs": page["discs"], "contributors": page["contributors"][:8],
                "title_markers": page["norm"]["markers"],
                "query_variants": page["norm"]["query_variants"][:4],
            },
            "imdb_signals": {"match_status": imdb.get("status"), "tconsts": imdb.get("tconsts", [])[:10],
                             "title_types": imdb.get("types", []),
                             "resolution": imdb.get("resolution")},
        }
        if res.get("candidate_movie_titles") is not None:
            evidence["candidate_movie_titles"] = res["candidate_movie_titles"]
        if asin in review_marks:
            evidence["manual_review"] = review_marks[asin]
        rows.append((
            asin, res["classification"], res["status"],
            float(res["confidence"]) if res.get("confidence") is not None else None,
            res.get("pending_reason"),
            page["product_title"] if page else None,
            None,  # snapshot_sha256 filled below
            imdb.get("tconsts", []) or None,
            imdb.get("status") if page else "not_attempted",
            (imdb.get("types") or [None])[0] if page and imdb.get("types") else None,
            json.dumps(evidence, ensure_ascii=False),
            RULES_VERSION, run_id,
        ))

    # snapshot sha256 from manifest evidence (fast: compute from file)
    import hashlib
    sha_by_asin = {}
    for asin, page in pages.items():
        h = hashlib.sha256()
        with gzip.open(page["snapshot_path"], "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        sha_by_asin[asin] = h.hexdigest()
    rows = [tuple(list(r[:6]) + [sha_by_asin.get(r[0])] + list(r[7:])) for r in rows]

    with psycopg.connect(**DB) as conn:
        sql = """
            INSERT INTO product_classification
                (asin, classification, classification_status, confidence, pending_reason,
                 page_title_raw, snapshot_sha256, candidate_tconsts, imdb_match_status,
                 imdb_title_type, evidence, rules_version, run_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
            ON CONFLICT (asin) DO UPDATE SET
                classification=EXCLUDED.classification,
                classification_status=EXCLUDED.classification_status,
                confidence=EXCLUDED.confidence,
                pending_reason=EXCLUDED.pending_reason,
                page_title_raw=EXCLUDED.page_title_raw,
                snapshot_sha256=EXCLUDED.snapshot_sha256,
                candidate_tconsts=EXCLUDED.candidate_tconsts,
                imdb_match_status=EXCLUDED.imdb_match_status,
                imdb_title_type=EXCLUDED.imdb_title_type,
                evidence=EXCLUDED.evidence,
                rules_version=EXCLUDED.rules_version,
                run_id=EXCLUDED.run_id,
                updated_at=now()
        """
        with conn.cursor() as cur:
            for i in range(0, len(rows), 500):
                cur.executemany(sql, rows[i:i + 500])

        # collection candidates -> product_movie_candidate
        cand_rows = []
        for asin, res in results.items():
            for t in res.get("candidate_movie_titles") or []:
                cand_rows.append((asin, t, "page_genre_list", None, RULES_VERSION, run_id))
        if cand_rows:
            conn.execute("DELETE FROM product_movie_candidate WHERE run_id = %s", (run_id,))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO product_movie_candidate "
                    "(asin, movie_title_raw, match_source, candidate_tconsts, rules_version, run_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", cand_rows)
        conn.commit()

        counts = dict(conn.execute(
            "SELECT classification, count(*) FROM product_classification GROUP BY 1").fetchall())
        st_counts = dict(conn.execute(
            "SELECT classification_status, count(*) FROM product_classification GROUP BY 1").fetchall())
        reason_counts = dict(conn.execute(
            "SELECT coalesce(pending_reason,'-'), count(*) FROM product_classification "
            "WHERE classification='pending_review' GROUP BY 1").fetchall())
        queue_states = dict(conn.execute(
            "SELECT status, count(*) FROM product_crawl_queue GROUP BY 1").fetchall())
        attempts = dict(conn.execute(
            "SELECT content_feature, count(*) FROM crawl_attempt GROUP BY 1").fetchall())

    # ---- field coverage over identified works ----
    identified = {a: r for a, r in results.items() if r["classification"] != "pending_review"}
    cov = {}
    for field, fn in {
        "title": lambda r: bool(r["page"] and r["page"]["product_title"]),
        "release_year(imdb.startYear)": lambda r: bool(r["imdb"].get("rows") and
                                                       r["imdb"]["rows"][0].get("startYear") not in (None, "\\N")),
        "genres": lambda r: bool((r["page"] and r["page"].get("genre")) or
                                 (r["imdb"].get("rows") and r["imdb"]["rows"][0].get("genres") not in (None, "\\N"))),
        "directors": lambda r: bool(r["page"] and any(c["role"] == "Director" for c in r["page"]["contributors"])),
        "starring/cast": lambda r: bool(r["page"] and any(c["role"] == "Actor" for c in r["page"]["contributors"])),
        "edition.format": lambda r: bool(r["page"] and r["page"].get("format")),
        "edition.language": lambda r: bool(r["page"] and r["page"].get("language")),
        "edition.runtime": lambda r: bool(r["page"] and r["page"].get("runtime_min")),
        "edition.discs": lambda r: bool(r["page"] and r["page"].get("discs")),
    }.items():
        n = sum(1 for r in identified.values() if fn(r))
        cov[field] = {"covered": n, "missing": len(identified) - n}
    # source separation for title/genres
    src_split = {
        "title": {"product_page": sum(1 for r in identified.values() if r["page"] and r["page"]["product_title"])},
        "release_year": {"imdb": sum(1 for r in identified.values() if r["imdb"].get("rows")
                                     and r["imdb"]["rows"][0].get("startYear") not in (None, "\\N"))},
        "genres": {"product_page": sum(1 for r in identified.values() if r["page"] and r["page"].get("genre")),
                   "imdb": sum(1 for r in identified.values() if r["imdb"].get("rows")
                               and r["imdb"]["rows"][0].get("genres") not in (None, "\\N"))},
    }

    # ---- samples (<=5 per class, high review_count first) ----
    samples = {c: [] for c in CLASSES}
    for asin, r in sorted(results.items(), key=lambda kv: -queue[kv[0]]["review_count"]):
        c = r["classification"]
        if len(samples[c]) >= 5:
            continue
        page, imdb = r.get("page"), r.get("imdb")
        samples[c].append({
            "asin": asin, "review_count": queue[asin]["review_count"],
            "title_raw": (page or {}).get("product_title"),
            "classification_status": r["status"], "confidence": r.get("confidence"),
            "pending_reason": r.get("pending_reason"),
            "rules_fired": r["rules_fired"], "rationale": r["rationale"],
            "imdb_match": imdb.get("status"), "imdb_types": imdb.get("types", []),
            "imdb_tconsts": (imdb.get("tconsts") or [])[:4],
            "imdb_resolution": imdb.get("resolution"),
            "runtime_min": (page or {}).get("runtime_min"),
            "genre": (page or {}).get("genre"),
            "department": (page or {}).get("department"),
        })

    # ---- review-weighted + rates ----
    total_reviews = sum(m["review_count"] for m in queue.values())
    rev_by_class = {c: 0 for c in CLASSES}
    for asin, r in results.items():
        rev_by_class[r["classification"]] += queue[asin]["review_count"]

    attempted = sum(v for k, v in queue_states.items() if k not in ("pending", "not_crawled"))
    success_q = queue_states.get("success", 0)

    stats = {
        "rules_version": RULES_VERSION, "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_seconds": round(time.time() - t0, 1),
        "queue_total": len(queue),
        "crawl_queue_states": queue_states,
        "crawl_attempt_features": attempts,
        "snapshot_files_total": len(snap_map),
        "snapshot_files_product_page_size": len(big),
        "classification_counts": counts,
        "classification_status_counts": st_counts,
        "pending_reason_counts": reason_counts,
        "review_weighted": {"total_reviews": total_reviews, "by_class": rev_by_class},
        "identified_works": len(identified),
        "identified_movie_class": sum(1 for r in results.values() if r["classification"] == "movie"),
        "collection_products": sum(1 for r in results.values() if r["classification"] == "collection"),
        "imdb_match_counts": {k: sum(1 for r in results.values() if r["imdb"].get("status") == k)
                              for k in ("unique", "unique_class", "ambiguous", "none", "not_attempted")},
        "field_coverage_identified_works": {"denominator": len(identified), "fields": cov,
                                            "source_split": src_split},
        "identification_coverage": {
            "identified_over_queue": f"{len(identified)}/{len(queue)}",
            "identified_review_share": round(sum(rev_by_class[c] for c in CLASSES if c != "pending_review")
                                            / total_reviews, 6) if total_reviews else None},
        "crawl_success_rate": {
            "口径": "offline_snapshot vs DB queue；实时采集未执行，单列",
            "attempted_asins": attempted, "success_asins": success_q,
            "success_rate_over_attempted": round(success_q / attempted, 6) if attempted else None,
            "access_restricted_rate": round(queue_states.get("access_restricted", 0) / attempted, 6) if attempted else None},
        "sources_separated": {
            "offline_snapshot": {"product_page_snapshots_classified": len(pages)},
            "third_party_imdb": {"works_with_imdb_candidates": sum(
                1 for r in results.values() if r["imdb"].get("tconsts"))},
            "realtime_collection": {"status": "not_performed", "count": 0}},
    }
    os.makedirs(os.path.dirname(OUT_STATS), exist_ok=True)
    with open(OUT_STATS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    with open(OUT_SAMPLES, "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)

    # ---- asin_movies.txt (movie + collection for run_scan_wave hook) ----
    movie_asins = sorted(
        (a for a, r in results.items() if r["classification"] in ("movie", "collection")),
        key=lambda a: queue[a]["priority"])
    os.makedirs(os.path.dirname(OUT_MOVIES), exist_ok=True)
    with open(OUT_MOVIES, "w", encoding="utf-8") as f:
        f.write(f"# task3 movie+collection ASINs  rules={RULES_VERSION}  run_id={run_id}\n")
        f.write(f"# generated_at_utc={stats['generated_at_utc']}  count={len(movie_asins)}\n")
        f.write("\n".join(movie_asins) + ("\n" if movie_asins else ""))

    print("classification:", counts)
    print("status:", st_counts)
    print("pending reasons:", reason_counts)
    print(f"identified works: {len(identified)}  movie: {stats['identified_movie_class']}  "
          f"collection: {stats['collection_products']}")
    print(f"wrote {OUT_STATS}")
    print(f"wrote {OUT_SAMPLES}")
    print(f"wrote {OUT_MOVIES} ({len(movie_asins)} asins)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
