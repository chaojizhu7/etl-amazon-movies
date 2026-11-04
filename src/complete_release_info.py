# -*- coding: utf-8 -*-
"""Task 6: 缺失上映信息补全（字段覆盖矩阵 §12 语义分层）。

语义分层（铁律：替代源不冒充）：
  ① 作品首映/上映年份：imdb.startYear → release_year（precision=year，不补 1 月 1 日）
  ② 某地区上映时间：有来源才记录（本版无 title.akas，实测 0 条）
  ③ 商品发售日期（DVD/Blu-ray）：产品页 → edition 域（asin 级），永不写入 release_year
  最早评论时间只作 earliest_review_time；条件性上界另存 inferred_release_bound
  （status=inferred + confidence）；查不到 → NULL + missing_reason（矩阵词表）。

Usage (from project root):
    .venv\\Scripts\\python.exe src\\complete_release_info.py [--run-id run_20261104-05]
"""
import argparse
import glob
import gzip
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import psycopg

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_DIR = os.path.join(ROOT, "logs", "summary")
PAGES = os.path.join(ROOT, "snapshots", "pages")
MOVIES_TXT = os.path.join(ROOT, "data", "raw", "movies.txt.gz")
DB = dict(host="localhost", port="5432", dbname="etl_movie", user="postgres", password="postgres")

DATE_PATTERNS = [
    ("date_first_available", re.compile(r"Date First Available[^0-9A-Za-z]{0,20}([A-Z][a-z]+ \d{1,2},\s*\d{4})", re.I)),
    ("release_date_label", re.compile(r"(?:Original )?Release date[^0-9A-Za-z]{0,20}([A-Z][a-z]+ \d{1,2},\s*\d{4})", re.I)),
]
FORMAT_LINE_RE = re.compile(r"^(?:DVD|Blu-?ray(?:\s*3D)?|HD\s*DVD|VHS|CD|4K\s*Ultra\s*HD)\b", re.I)
DATE_INLINE_RE = re.compile(r"([A-Z][a-z]+ \d{1,2},\s*(?:19|20)\d{2})")
MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
          "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}


def iso_from_us(raw):
    m = re.match(r"([A-Za-z]+) (\d{1,2}),\s*(\d{4})", raw or "")
    if not m:
        return None
    mo = MONTHS.get(m.group(1).lower())
    if not mo:
        return None
    return f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(2)):02d}"


def min_review_times(target_asins):
    """流式 movies.txt.gz：目标 ASIN 的最早 review 时间（unix 秒）。"""
    mins = {}
    cur_id, cur_t = None, None

    def flush():
        if cur_id in target_asins and cur_t is not None:
            if cur_id not in mins or cur_t < mins[cur_id]:
                mins[cur_id] = cur_t

    with gzip.open(MOVIES_TXT, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("product/productId:"):
                flush()
                cur_id = line.split(":", 1)[1].strip()
                cur_t = None
            elif line.startswith("review/time:"):
                v = line.split(":", 1)[1].strip()
                if v.lstrip("-").isdigit():
                    cur_t = int(v)
    flush()
    return mins


def edition_sale_date(asin):
    """产品页发售/上架日期（edition 域；原文保留）。
    优先带标签字段；否则取版本选择行『格式行 + 日期行』（DVD | May 22, 2012 型）。"""
    p = os.path.join(PAGES, f"{asin}.html.gz")
    if not os.path.isfile(p):
        return None, "no_snapshot"
    with gzip.open(p, "rt", encoding="utf-8", errors="replace") as f:
        html = f.read()
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I))
    text = re.sub(r"\s+", " ", text)
    for rule, pat in DATE_PATTERNS:
        m = pat.search(text)
        if m:
            raw = m.group(1)
            iso = iso_from_us(raw)
            if iso:
                return {"raw": raw, "iso": iso, "rule": f"product_page:{rule}"}, None
    # 版本选择行：格式名行 → 日期行
    lines = [re.sub(r"\s+", " ", l).strip() for l in
             re.sub(r"<[^>]+>", "\n", re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I)).splitlines()]
    lines = [l for l in lines if l]
    for i, l in enumerate(lines):
        if FORMAT_LINE_RE.match(l):
            for j in range(i + 1, min(i + 3, len(lines))):
                m = DATE_INLINE_RE.fullmatch(lines[j]) or DATE_INLINE_RE.match(lines[j])
                if m and len(lines[j]) <= 24:
                    raw = m.group(1)
                    iso = iso_from_us(raw)
                    if iso:
                        return {"raw": raw, "iso": iso,
                                "rule": "product_page:edition_row_date"}, None
    return None, "date_not_on_page"


def main(run_id):
    t0 = time.time()
    with psycopg.connect(**DB) as conn:
        with open(os.path.join(ROOT, "sql", "06_release_completion.sql"), encoding="utf-8") as f:
            conn.execute(f.read())
        movies = {}
        for mid, ext in conn.execute("SELECT movie_id, external_ids FROM movie_entity"):
            movies[mid] = {"tconsts": (ext or {}).get("imdb", [])}
        members = {}
        for asin, mid in conn.execute(
                "SELECT asin, movie_id FROM product_movie WHERE relation_type='single'"):
            members.setdefault(mid, []).append(asin)
        # 任务3/4 证据：resolved tconst + 快照哈希
        ev_rows = conn.execute(
            "SELECT asin, evidence->'imdb_signals' FROM product_classification").fetchall()
    ev_map = {}
    for asin, im in ev_rows:
        if im:
            ev_map[asin] = {
                "tconsts": im.get("tconsts") or [],
                "resolved": (im.get("resolution") or {}).get("tconst")}

    # tconst → startYear（imdb_title_cache）
    by_t = {}
    cache_path = os.path.join(OUT_DIR, "imdb_title_cache.json")
    if os.path.isfile(cache_path):
        for rows in json.load(open(cache_path, encoding="utf-8")).values():
            for r in rows:
                by_t.setdefault(r["tconst"], r)

    target_asins = {a for ms in members.values() for a in ms}
    print(f"movies={len(movies)}  member asins={len(target_asins)}  最早评论时间扫描 movies.txt.gz ...")
    mins = min_review_times(target_asins)
    print(f"  review-min found for {len(mins)} asins")

    def year_of(t):
        y = (by_t.get(t) or {}).get("startYear")
        if y in (None, "\\N"):
            return None
        try:
            return int(y)
        except ValueError:
            return None

    facts = []

    def add(mid, asin, ftype, raw, norm, prec, src, loc, rule, conf, status, reason=None):
        facts.append((mid, asin or "", ftype, raw, norm, prec, src, loc,
                      rule, conf, status, reason, run_id))

    regional = 0
    edition_found = edition_missing = 0
    for mid, info in movies.items():
        asins = members.get(mid, [])
        # ① release_year
        resolved_years = []
        for a in asins:
            e = ev_map.get(a) or {}
            t = e.get("resolved") or (e["tconsts"][:1] if len(e.get("tconsts") or []) == 1 else [None])[0]
            y = year_of(t) if t else None
            if y:
                resolved_years.append((y, t))
        cand_years = {year_of(t) for t in info["tconsts"]} - {None}
        loc = None
        if resolved_years:
            ys = {y for y, _ in resolved_years}
            if len(ys) == 1:
                y = ys.pop()
                loc = ",".join(sorted({t for _, t in resolved_years}))[:80]
                add(mid, "", "release_year", str(y), str(y), "year", "imdb", loc,
                    "imdb.startYear(resolved_tconst；成员间一致)", 0.90, "verified")
            else:
                add(mid, "", "release_year", None, None, "none", "imdb", loc,
                    "imdb.startYear(resolved_tconst；成员年份冲突)", None, "missing", "pending_review")
        elif cand_years:
            if len(cand_years) == 1:
                y = cand_years.pop()
                add(mid, "", "release_year", str(y), str(y), "year", "imdb",
                    ",".join(info["tconsts"])[:80],
                    "imdb.startYear(候选 tconst 全体一致)", 0.85, "verified")
            else:
                add(mid, "", "release_year", None, None, "none", "imdb",
                    ",".join(info["tconsts"])[:80],
                    "imdb.startYear(候选年份冲突)", None, "missing", "pending_review")
        elif info["tconsts"]:
            add(mid, "", "release_year", None, None, "none", "imdb",
                ",".join(info["tconsts"])[:80], "imdb.startYear(缺失)", None,
                "missing", "year_missing_in_imdb")
        else:
            add(mid, "", "release_year", None, None, "none", "imdb", None,
                "无 tconst 匹配", None, "missing", "no_imdb_match")

        # ④ earliest_review_time + ⑤ inferred_release_bound
        times = [mins[a] for a in asins if a in mins]
        if times:
            tmin = min(times)
            iso = datetime.fromtimestamp(tmin, tz=timezone.utc).strftime("%Y-%m-%d")
            add(mid, "", "earliest_review_time", str(tmin), iso, "day", "review",
                "movies.txt review/time", "min(review.time) over member asins", 1.0, "verified")
            add(mid, "", "inferred_release_bound", iso, iso, "day", "system",
                "derived:earliest_review_time",
                "release ≤ earliest_review_time（条件性上界）", 0.70, "inferred")
        else:
            add(mid, "", "earliest_review_time", None, None, "none", "review",
                "movies.txt review/time", "min(review.time) 无记录", None,
                "missing", "empty_user")

        # ③ edition_sale_date（asin 级，绝不写入 release_year）
        for a in asins:
            d, err = edition_sale_date(a)
            if d:
                add(mid, a, "edition_sale_date", d["raw"], d["iso"], "day", "product_page",
                    a, d["rule"], 0.90, "verified")
                edition_found += 1
            else:
                edition_missing += 1
                add(mid, a, "edition_sale_date", None, None, "none", "product_page", a,
                    "product_page:date_scan", None, "missing",
                    "page_unreachable" if err == "no_snapshot" else "pending_review")

    # ② regional_release_date：本版无 title.akas / 无地区来源 → 0 条（如实统计）
    with psycopg.connect(**DB) as conn:
        with conn.cursor() as cur:
            for row in facts:
                cur.execute(
                    "INSERT INTO release_facts (movie_id,asin,fact_type,value_raw,value_norm,"
                    "precision,source,source_locator,match_rule,confidence,status,missing_reason,run_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (movie_id,asin,fact_type,run_id) DO UPDATE SET "
                    "value_raw=EXCLUDED.value_raw, value_norm=EXCLUDED.value_norm, "
                    "precision=EXCLUDED.precision, source=EXCLUDED.source, "
                    "source_locator=EXCLUDED.source_locator, match_rule=EXCLUDED.match_rule, "
                    "confidence=EXCLUDED.confidence, status=EXCLUDED.status, "
                    "missing_reason=EXCLUDED.missing_reason, extracted_at=now()", row)
        conn.commit()
        st = {}
        for t, s, n in conn.execute(
                "SELECT fact_type, status, count(*) FROM release_facts WHERE run_id=%s "
                "GROUP BY 1,2", (run_id,)):
            st.setdefault(t, {})[s] = n
        mr = dict(conn.execute(
            "SELECT coalesce(missing_reason,'-'), count(*) FROM release_facts "
            "WHERE run_id=%s AND status='missing' GROUP BY 1", (run_id,)).fetchall())
        pr = dict(conn.execute(
            "SELECT coalesce(precision,'-'), count(*) FROM release_facts "
            "WHERE run_id=%s AND fact_type='release_year' GROUP BY 1", (run_id,)).fetchall())
        rules = dict(conn.execute(
            "SELECT match_rule, count(*) FROM release_facts "
            "WHERE run_id=%s AND fact_type='release_year' AND status='verified' "
            "GROUP BY 1", (run_id,)).fetchall())

    stats = {
        "run_id": run_id, "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_seconds": round(time.time() - t0, 1),
        "movies_total": len(movies),
        "release_year": {"filled": st.get("release_year", {}).get("verified", 0),
                         "missing": st.get("release_year", {}).get("missing", 0),
                         "precision_distribution": pr,
                         "fill_rules": rules},
        "earliest_review_time": st.get("earliest_review_time", {}),
        "inferred_release_bound": st.get("inferred_release_bound", {}),
        "edition_sale_date": {"found": edition_found, "missing": edition_missing},
        "regional_release_date": {"recorded": regional, "note": "无 title.akas/地区来源，未记录"},
        "missing_reasons": mr,
        "semantics_guard": "商品发售日期仅入 edition 域（asin 级 fact_type=edition_sale_date），永不写入 release_year；仅年份不补 1 月 1 日",
    }
    spath = os.path.join(OUT_DIR, "release_completion_stats.json")
    json.dump(stats, open(spath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("==== 补全完成 ====")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"wrote {spath}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="run_20261104-05")
    a = ap.parse_args()
    sys.exit(main(a.run_id))
