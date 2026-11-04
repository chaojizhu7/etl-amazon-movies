# -*- coding: utf-8 -*-
"""Task 7: 字段级来源冲突选择（rules/field_selection_rules_v1.md v1.0）。

为每个字段建立可复核候选断言（保留全部候选+来源），按规则链 R1–R7 选定/拒绝/保留冲突。
决策统计：自动决策数（rule_auto）/ 人工决策数（manual_inherit）/ 保留冲突数。

Usage (from project root):
    .venv\\Scripts\\python.exe src\\select_fields.py [--run-id run_20261104-07]
"""
import argparse
import gzip
import html as html_mod
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import psycopg

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_DIR = os.path.join(ROOT, "logs", "summary")
DB = dict(host="localhost", port="5432", dbname="etl_movie", user="postgres", password="postgres")
RULES_VERSION = "field_sel_v1.0"

CATEGORY_WORDS = {
    "action & adventure", "kids & family", "science fiction & fantasy", "blu-ray movie",
    "dvd movie", "comedy", "drama", "musical", "musicals & performing arts", "animation",
    "anime & manga", "mystery & thrillers", "westerns", "military & war", "horror",
    "romance", "documentary", "classics", "fantasy", "music videos & concerts",
    "concerts & music videos", "fitness", "comedy/stand-up", "drama/love & romance",
    "exercise & fitness", "sports", "special interest", "education", "television",
    "science fiction", "adventure", "war", "epics", "thrillers", "mystery", "suspense",
}


def canon(t):
    t = re.sub(r"[^0-9a-zà-ÿ ]", " ", (t or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def name_canon(t):
    """人名规范形：去 HTML 实体、词序无关（Last, First ≡ First Last）、
    去后缀（Jr/Sr/II/III）与中间名缩写（单字母）——仅用于比对。"""
    toks = canon(html_mod.unescape(t or "")).split()
    toks = [x for x in toks if len(x) > 1 and x not in ("jr", "sr", "ii", "iii", "iv")]
    return " ".join(sorted(toks))


def main(run_id):
    t0 = time.time()
    title_cache = {}
    for rows in json.load(open(os.path.join(OUT_DIR, "imdb_title_cache.json"), encoding="utf-8")).values():
        for r in rows:
            title_cache.setdefault(r["tconst"], r)
    name_cache = {}
    sc = os.path.join(OUT_DIR, "person_source_cache.json")
    if os.path.isfile(sc):
        name_cache = json.load(open(sc, encoding="utf-8")).get("names", {})

    with psycopg.connect(**DB) as conn:
        with open(os.path.join(ROOT, "sql", "07_field_selection.sql"), encoding="utf-8") as f:
            conn.execute(f.read())
        conn.execute("DELETE FROM field_assertion WHERE run_id=%s", (run_id,))
        movies = {r[0]: (r[1] or {}).get("imdb", []) for r in
                  conn.execute("SELECT movie_id, external_ids FROM movie_entity")}
        members = {}
        for asin, mid in conn.execute(
                "SELECT asin, movie_id FROM product_movie WHERE relation_type IN ('single','collection_member')"):
            members.setdefault(mid, []).append(asin)
        aliases = {}
        for mid, raw, src, loc in conn.execute(
                "SELECT movie_id, title_raw, source, source_locator FROM movie_alias"):
            aliases.setdefault(mid, []).append((raw, src, loc))
        manual_movies = {r[0] for r in conn.execute(
            "SELECT DISTINCT movie_id FROM movie_alias WHERE source='manual'")}
        ev = {}
        for asin, im, pg in conn.execute(
                "SELECT asin, evidence->'imdb_signals', evidence->'page_signals' "
                "FROM product_classification WHERE evidence ? 'page_signals'"):
            ev[asin] = {"im": im or {}, "pg": pg or {}}
        facts = conn.execute(
            "SELECT movie_id, fact_type, value_raw, value_norm, status, match_rule, confidence "
            "FROM release_facts WHERE fact_type='release_year'").fetchall()
        crew = json.load(open(sc, encoding="utf-8")).get("crew", {}) if os.path.isfile(sc) else {}
        prins = json.load(open(sc, encoding="utf-8")).get("principals", {}) if os.path.isfile(sc) else {}

    assertions = []

    def add(mid, field, raw, norm, role, src, loc, rule, conf, status, basis=None, rej=None):
        assertions.append((mid, field, raw, norm, role, src, loc, rule,
                           conf, status, basis, rej, RULES_VERSION, run_id))

    def resolved_t(mid):
        """成员证据中已消歧 tconst，其次唯一候选。"""
        ts = set()
        for a in members.get(mid, []):
            im = (ev.get(a) or {}).get("im", {})
            r = (im.get("resolution") or {}).get("tconst")
            if r:
                ts.add(r)
        if ts:
            return sorted(ts), "resolved_tconst"
        cand = movies.get(mid) or []
        if len(cand) == 1:
            return cand, "single_candidate"
        return [], "ambiguous"

    year_by_movie = {m: (raw, norm, st, rule, conf) for m, ft, raw, norm, st, rule, conf in facts
                     if ft == "release_year"}

    for mid in movies:
        ts, anchor = resolved_t(mid)
        basis = "manual_inherit" if mid in manual_movies else "rule_auto"

        # ---- title ----
        imdb_title = title_cache.get(ts[0], {}).get("primaryTitle") if ts else None
        selected_title = None
        if imdb_title:
            selected_title = imdb_title
            add(mid, "title", imdb_title, imdb_title, "canonical", "imdb", ts[0],
                f"R2({anchor})+R3 imdb primaryTitle", 0.95 if anchor == "resolved_tconst" else 0.90,
                "selected", basis)
        page_titles = []
        for a in members.get(mid, []):
            t = (ev.get(a) or {}).get("pg", {}).get("product_title")
            if t:
                page_titles.append((t, a))
        if page_titles and not selected_title:
            t, a = page_titles[0]
            selected_title = t
            add(mid, "title", t, t, "canonical", "product_page", a,
                "R2(唯一来源)+R3 product_page title", 0.85, "selected", basis)
        for t, a in page_titles:
            if selected_title and canon(t) != canon(selected_title):
                add(mid, "title", t, t, "alias", "product_page", a,
                    "R3 低权威保留为 alias（不删除）", None, "candidate")
        for raw, src, loc in aliases.get(mid, []):
            if selected_title and canon(raw) != canon(selected_title):
                add(mid, "title", raw, raw, "alias", src, loc,
                    "R7" if src == "manual" else "R3 alias 保留",
                    1.0 if src == "manual" else None,
                    "candidate", "manual_inherit" if src == "manual" else None)

        # ---- release_year ----
        yr = year_by_movie.get(mid)
        if yr and yr[2] == "verified":
            add(mid, "release_year", yr[0], yr[1], "canonical", "imdb",
                ",".join(ts or movies.get(mid, []))[:80], f"R2({yr[3]})+R5 precision=year",
                yr[4], "selected", basis)
            # 产品页年份线索若有 → 保留（不覆盖）
        elif yr and yr[2] == "missing" and yr[1] is None:
            ys = {}
            for t in (movies.get(mid) or []):
                y = title_cache.get(t, {}).get("startYear")
                if y and y != "\\N":
                    ys.setdefault(y, []).append(t)
            for y, tlist in sorted(ys.items()):
                add(mid, "release_year", y, y, "candidate_year", "imdb", ",".join(tlist)[:80],
                    "R2 候选分歧，全部保留（不挑一个）", None, "conflict")

        # ---- genres（多值合并，角色分离）----
        for t in ts:
            g = title_cache.get(t, {}).get("genres") or ""
            for item in g.split(","):
                item = item.strip()
                if item and item != "\\N":
                    add(mid, "genres", item, item.lower(), "work_genre", "imdb", t,
                        f"R2({anchor})+R4 角色=work_genre", 0.9, "selected", basis)
        seen_page_g = set()
        for a in members.get(mid, []):
            g = (ev.get(a) or {}).get("pg", {}).get("genre") or ""
            for item in html_split(g):
                key = canon(item)
                if key and key not in seen_page_g:
                    seen_page_g.add(key)
                    add(mid, "genres", item, item.lower(), "seller_category_tag", "product_page", a,
                        "R4 角色=seller_category_tag（标签不冒充作品类型）", 0.7, "selected", basis)

        # ---- directors / starring（imdb 权威 + 产品页署名保留）----
        for field, role_work, role_page in (("directors", "work_crew", "page_credit"),
                                            ("starring", "work_cast", "page_credit")):
            imdb_names = set()
            for t in ts:
                if field == "directors":
                    for nc in (crew.get(t) or {}).get("directors", []):
                        nm = (name_cache.get(nc) or {}).get("primaryName")
                        if nm:
                            imdb_names.add(nm)
                            add(mid, field, nm, name_canon(nm), role_work, "imdb", f"{t}/{nc}",
                                f"R2({anchor})+R3 imdb crew/principals", 0.9, "selected", basis)
                else:
                    for row in (prins.get(t) or [])[:10]:
                        if row.get("category") in ("actor", "actress"):
                            nm = (name_cache.get(row["nconst"]) or {}).get("primaryName")
                            if nm:
                                imdb_names.add(nm)
                                add(mid, field, nm, name_canon(nm), role_work, "imdb",
                                    f"{t}/{row['nconst']}", "R3 imdb principals(ordering≤10)",
                                    0.9, "selected", basis)
            imdb_canon = {name_canon(n) for n in imdb_names}
            seen_pg = set()
            for a in members.get(mid, []):
                for c in ((ev.get(a) or {}).get("pg", {}).get("contributors") or []):
                    want = "Director" if field == "directors" else "Actor"
                    if c.get("role") != want:
                        continue
                    nm = c.get("name") or ""
                    k = name_canon(nm)
                    if not k or k in seen_pg:
                        continue
                    seen_pg.add(k)
                    if k in imdb_canon:
                        add(mid, field, nm, k, role_page, "product_page", a,
                            "R4 与 imdb 署名一致（页面署名=佐证）", 0.8, "selected", basis)
                    elif imdb_canon:
                        add(mid, field, nm, k, role_page, "product_page", a,
                            "R4 页面署名不在 imdb 集内 → conflict 保留（不删不编造）",
                            None, "conflict")
                    else:
                        add(mid, field, nm, k, role_page, "product_page", a,
                            "R3 无 imdb 源，页面署名保留为候选", None, "candidate")

    with psycopg.connect(**DB) as conn:
        with conn.cursor() as cur:
            for row in assertions:
                cur.execute(
                    "INSERT INTO field_assertion (movie_id,field_name,value_raw,value_norm,"
                    "value_role,source,source_locator,match_rule,confidence,status,decision_basis,"
                    "reject_reason,rules_version,run_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (movie_id,field_name,value_norm,value_role,source,run_id) DO UPDATE SET "
                    "status=EXCLUDED.status, decision_basis=EXCLUDED.decision_basis, "
                    "match_rule=EXCLUDED.match_rule, confidence=EXCLUDED.confidence", row)
        conn.commit()
        st = {}
        for f, s, b, n in conn.execute(
                "SELECT field_name, status, coalesce(decision_basis,'-'), count(*) "
                "FROM field_assertion WHERE run_id=%s GROUP BY 1,2,3", (run_id,)):
            st.setdefault(f, {}).setdefault(s, {})[b] = n
        tot_auto = conn.execute(
            "SELECT count(*) FROM field_assertion WHERE run_id=%s AND status='selected' "
            "AND decision_basis='rule_auto'", (run_id,)).fetchone()[0]
        tot_man = conn.execute(
            "SELECT count(*) FROM field_assertion WHERE run_id=%s AND decision_basis='manual_inherit'",
            (run_id,)).fetchone()[0]
        tot_conf = conn.execute(
            "SELECT count(*) FROM field_assertion WHERE run_id=%s AND status='conflict'",
            (run_id,)).fetchone()[0]
        # 可查询性验证（要求6）
        chk = {
            "product_movie_rows": conn.execute("SELECT count(*) FROM product_movie").fetchone()[0],
            "release_facts_rows": conn.execute("SELECT count(*) FROM release_facts").fetchone()[0],
            "movie_alias_rows": conn.execute("SELECT count(*) FROM movie_alias").fetchone()[0],
            "movie_person_rows": conn.execute("SELECT count(*) FROM movie_person").fetchone()[0],
            "decision_rows": conn.execute("SELECT count(*) FROM movie_merge_decision").fetchone()[0],
        }
        conflicts = conn.execute(
            "SELECT movie_id, field_name, value_raw, value_role, source, match_rule "
            "FROM field_assertion WHERE run_id=%s AND status='conflict' "
            "ORDER BY field_name, movie_id LIMIT 12", (run_id,)).fetchall()

    stats = {
        "run_id": run_id, "rules_version": RULES_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_seconds": round(time.time() - t0, 1),
        "assertions_total": len(assertions),
        "by_field": st,
        "decision_stats": {"auto_rule_auto_selected": tot_auto,
                           "manual_inherit": tot_man, "conflict_retained": tot_conf},
        "queryability_check": chk,
    }
    sp = os.path.join(OUT_DIR, "field_selection_stats.json")
    json.dump(stats, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("==== 字段选择完成 ====")
    print(json.dumps(stats["decision_stats"], ensure_ascii=False))
    print("by_field:", json.dumps(st, ensure_ascii=False))
    print("queryability:", chk)
    print("conflict samples:")
    for c in conflicts:
        print("  ", c)
    print(f"wrote {sp}")
    return 0


def html_split(g):
    return [x.strip() for x in re.split(r",", g) if x.strip()]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="run_20261104-07")
    a = ap.parse_args()
    sys.exit(main(a.run_id))
