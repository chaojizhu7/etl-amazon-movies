# -*- coding: utf-8 -*-
"""Task 4: movie entity merge (rules/merge_rules_v1.md v1.1).

Mode --estimate: 合并预估报告（只算不写库）——候选对生成 → 6 信号验证 →
三级判定计数（auto_merge / manual_review / no_merge）→ 链一致性预检。
执行模式（写库）待预估报告人工确认后启用。

Usage (from project root):
    .venv\\Scripts\\python.exe src\\merge_movies.py --estimate
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import psycopg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify_products import (  # noqa: E402
    ROOT, canon, normalize_title, ensure_cast_cache, load_cache, _name_toks, _name_match,
)

DB = dict(host="localhost", port="5432", dbname="etl_movie", user="postgres", password="postgres")
RULES_VERSION = "merge_v1.1"
OUT_DIR = os.path.join(ROOT, "logs", "summary")

# ---- weights & thresholds (merge_rules_v1.md §5/§6) ----
W = {"year": 0.30, "director": 0.28, "cast": 0.22, "runtime": 0.12, "genre": 0.08}
BASE = {"T": 0.85, "V": 0.65, "conf": 0.50, "strong": 2, "known": 3}
RELAX_A = dict(BASE, V=0.55)          # 放宽 V
RELAX_B = dict(BASE, known=2)         # 放宽 known
RELAX_AB = dict(BASE, V=0.55, known=2)

ORD_RE = re.compile(r"\s+(?:(\d{1,2})|([ivxl]{1,5})|part\s+(\d{1,2}))$", re.I)
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}


def ordinal_of(base):
    """Trailing sequel ordinal (1-2 digits / roman / 'part N'); 2049-style 4-digit years excluded."""
    m = ORD_RE.search(base or "")
    if not m:
        return None, base
    if m.group(1):
        n = int(m.group(1))
    elif m.group(2):
        n = ROMAN.get(m.group(2).lower())
    else:
        n = int(m.group(3))
    if n is None or n < 2:
        return None, base          # "1"/"i" 不作续集标记
    return n, base[:m.start()].strip()


def tokens(canon_str):
    return set((canon_str or "").split())


def jaccard(a, b):
    u = a | b
    return (len(a & b) / len(u)) if u else 0.0


def name_sets(names):
    out = []
    for n in names or []:
        t = _name_toks(n)
        if t:
            out.append(t)
    return out


def overlap_names(a, b):
    """Count of names in a matched by some name in b (greedy, distinct)."""
    used, cnt = set(), 0
    for ta in a:
        for j, tb in enumerate(b):
            if j not in used and _name_match(ta, tb):
                used.add(j)
                cnt += 1
                break
    return cnt


# ---------------- node building ----------------
def build_nodes():
    with psycopg.connect(**DB) as conn:
        rows = conn.execute(
            "SELECT asin, page_title_raw, evidence FROM product_classification "
            "WHERE classification = 'movie'").fetchall()
        coll = conn.execute(
            "SELECT asin, movie_title_raw FROM product_movie_candidate").fetchall()

    title_cache = load_cache()          # query -> rows
    by_tconst = {}
    for rows_ in title_cache.values():
        for r in rows_:
            by_tconst.setdefault(r["tconst"], r)

    nodes = []
    all_tconsts = set()
    for asin, title_raw, ev in rows:
        ev = ev or {}
        pg = ev.get("page_signals") or {}
        im = ev.get("imdb_signals") or {}
        tconsts = im.get("tconsts") or []
        all_tconsts.update(tconsts)
        res_t = (im.get("resolution") or {}).get("tconst")
        norm = normalize_title(title_raw or pg.get("product_title") or "")
        base = norm["base"]
        ord_, base_wo = ordinal_of(base)
        alias = {canon(a) for a in (norm.get("alias_candidates") or []) if a}
        alias |= {canon(v) for v in (norm.get("query_variants") or []) if v}
        trows = [by_tconst[t] for t in tconsts if t in by_tconst]
        # work-level fields: prefer resolved/unanimous tconst rows
        year = runtime_i = None
        genres_i, dirs_i, cast_i = set(), set(), set()
        sel = [r for r in trows if r["tconst"] == res_t] or trows
        years = {r.get("startYear") for r in sel if r.get("startYear") not in (None, "\\N")}
        if len(years) == 1:
            year = int(years.pop())
        runtimes = {r.get("runtimeMinutes") for r in sel if r.get("runtimeMinutes") not in (None, "\\N")}
        if len(runtimes) == 1:
            try:
                runtime_i = int(runtimes.pop())
            except ValueError:
                pass
        for r in sel:
            for g in (r.get("genres") or "").split(","):
                if g and g != "\\N":
                    genres_i.update(tokens(canon(g)))
        nodes.append({
            "kind": "edition", "id": asin, "title_raw": title_raw,
            "title_canon": canon(norm["base"]), "base_wo_ord": canon(base_wo),
            "alias": alias, "ord": ord_, "tconsts": tconsts, "resolved_t": res_t,
            "year": year, "year_src": "imdb" if year else None,
            "runtime": runtime_i if runtime_i else pg.get("runtime_min"),
            "genres": genres_i | tokens(canon(pg.get("genre") or "")),
            "dirs": name_sets([c["name"] for c in (pg.get("contributors") or []) if c.get("role") == "Director"]),
            "cast": name_sets([c["name"] for c in (pg.get("contributors") or []) if c.get("role") == "Actor"]),
            "multi_work": bool((pg.get("title_markers") or {}).get("multi_work_title")),
            "manual": ev.get("manual_review") or {},
            "tokens": tokens(canon(norm["base"])),
        })
    # enrich director/cast from imdb cast cache
    if all_tconsts:
        cache = ensure_cast_cache(sorted(all_tconsts))
        for n in nodes:
            for t in n["tconsts"]:
                c = cache.get(t) or {}
                n["dirs"] += name_sets(c.get("directors"))
                n["cast"] += name_sets(c.get("cast"))

    cand_nodes = []
    for asin, ct in coll:
        norm = normalize_title(ct or "")
        cand_nodes.append({
            "kind": "candidate", "id": f"{asin}::{ct}", "title_raw": ct,
            "title_canon": canon(norm["base"]), "base_wo_ord": canon(norm["base"]),
            "alias": {canon(a) for a in (norm.get("alias_candidates") or []) if a},
            "ord": None, "tokens": tokens(canon(norm["base"])),
            "year": None, "runtime": None, "genres": set(), "dirs": [], "cast": [],
            "multi_work": False, "manual": {}, "tconsts": [], "resolved_t": None,
        })
    return nodes, cand_nodes


# ---------------- signals (merge_rules §4) ----------------
def signals_for(a, b):
    sig, hard = {}, []
    ja = jaccard(a["tokens"], b["tokens"])
    alias_eq = bool(a["alias"] & (b["alias"] | {b["title_canon"]})) or \
        bool(b["alias"] & (a["alias"] | {a["title_canon"]})) or \
        (a["title_canon"] in b["alias"]) or (b["title_canon"] in a["alias"])
    canon_eq = a["title_canon"] == b["title_canon"]
    if canon_eq or alias_eq:
        sig["title"], t_coeff = 1.0, 1.0
    elif ja >= 0.85:
        sig["title"], t_coeff = 0.5, 0.7
    elif ja >= 0.5:
        sig["title"], t_coeff = 0.5, 0.6
    else:
        sig["title"], t_coeff = (-1.0 if not alias_eq else 0.5), 0.5
    title_high = canon_eq or alias_eq or ja >= 0.85

    # S2 year（v1.1 两档）
    year_forced = None
    if a["year"] and b["year"]:
        d = abs(a["year"] - b["year"])
        if d == 0:
            sig["year"] = 1.0
        elif d <= 3:
            sig["year"] = 0.5
        elif d <= 9:
            sig["year"] = -1.0
        elif title_high:
            sig["year"] = -1.0
            year_forced = "manual_review:year_gap_title_similar"
        elif ja < 0.5 and not alias_eq:
            hard.append("year_gap_title_divergent")
            sig["year"] = -1.0
        else:
            sig["year"] = -1.0
    else:
        sig["year"] = 0.0

    # S3 director
    if a["dirs"] and b["dirs"]:
        if overlap_names(a["dirs"], b["dirs"]):
            sig["director"] = 1.0
        else:
            sig["director"] = -1.0
    elif a["dirs"] or b["dirs"]:
        sig["director"] = 0.5
    else:
        sig["director"] = 0.0

    # S4 cast
    if a["cast"] and b["cast"]:
        ov = overlap_names(a["cast"], b["cast"])
        if ov >= 2:
            sig["cast"] = 1.0
        elif ov == 1:
            sig["cast"] = 0.5
        elif len(a["cast"]) >= 3 and len(b["cast"]) >= 3:
            sig["cast"] = -1.0
        else:
            sig["cast"] = -1.0
    else:
        sig["cast"] = 0.0

    # S5 runtime
    ra, rb = a["runtime"], b["runtime"]
    if ra and rb:
        diff = abs(ra - rb)
        frac = diff / max(ra, rb)
        if diff <= 10 or frac <= 0.10:
            sig["runtime"] = 1.0
        elif frac <= 0.40:
            sig["runtime"] = 0.5
        else:
            sig["runtime"] = -1.0
        if max(ra, rb) / min(ra, rb) > 1.6 and (a["multi_work"] or b["multi_work"]):
            hard.append("runtime_multiwork_mismatch")
    else:
        sig["runtime"] = 0.0

    # S6 genre
    if a["genres"] and b["genres"]:
        jg = jaccard(a["genres"], b["genres"])
        if jg >= 0.5:
            sig["genre"] = 1.0
        elif jg > 0:
            sig["genre"] = 0.5
        elif len(a["genres"]) >= 2 and len(b["genres"]) >= 2:
            sig["genre"] = -1.0
        else:
            sig["genre"] = 0.0
    else:
        sig["genre"] = 0.0

    # 硬冲突：序数不一致（续集）
    if a["base_wo_ord"] == b["base_wo_ord"] and a["ord"] != b["ord"]:
        hard.append("sequel_ordinal_mismatch")
    # 硬冲突：不同导演 + 主演零重合（重拍模式）
    if sig["director"] == -1.0 and sig["cast"] == -1.0:
        hard.append("director_diff_cast_empty")

    verify_keys = ("year", "director", "cast", "runtime", "genre")
    known = [k for k in verify_keys if sig[k] != 0.0]
    V = (sum(W[k] * sig[k] for k in known) / sum(W[k] for k in known)) if known else 0.0
    conf = t_coeff * (0.30 + 0.70 * max(V, 0.0))
    return sig, hard, V, conf, t_coeff, year_forced, len(known)


def decide(a, b, thresholds):
    sig, hard, V, conf, T, year_forced, known = signals_for(a, b)
    strong = sum(1 for k in ("year", "director", "cast") if sig[k] == 1.0)
    base_reason = None
    if hard:
        return "no_merge", "hard:" + "+".join(hard), sig, V, conf, known, strong
    if a["manual"].get("verdict") in ("wrong_match",) or b["manual"].get("verdict") in ("wrong_match",):
        if conf >= thresholds["conf"]:
            return "manual_review", "task3_wrong_match_pending", sig, V, conf, known, strong
    if year_forced:
        return "manual_review", "year_gap_title_similar", sig, V, conf, known, strong
    if sig["title"] == -1.0:
        return "no_merge", "title_conflict", sig, V, conf, known, strong
    if (T >= thresholds["T"] and V >= thresholds["V"] and strong >= thresholds["strong"]
            and known >= thresholds["known"]):
        return "auto_merge", "all_gates_pass", sig, V, conf, known, strong
    if conf < thresholds["conf"]:
        return "no_merge", "conf_below_floor", sig, V, conf, known, strong
    base_reason = "partial_signals"
    if V >= thresholds["V"] and strong < thresholds["strong"]:
        base_reason = "strong_signals_lt_2"
    elif known < thresholds["known"]:
        base_reason = "known_signals_lt_3"
    elif T < thresholds["T"]:
        base_reason = "title_gate_weak"
    return "manual_review", base_reason, sig, V, conf, known, strong


# ---------------- chain consistency (§7) ----------------
def chain_check(auto_pairs):
    """auto_pairs: list of (keyA, keyB, conf). Returns accepted pairs, rejected edges."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pairs_sorted = sorted(auto_pairs, key=lambda p: (-p[2], p[0], p[1]))
    all_keys = sorted({k for p in auto_pairs for k in (p[0], p[1])})
    edges = {(min(p[0], p[1]), max(p[0], p[1])): p[2] for p in auto_pairs}
    accepted, rejected = [], []
    for a, b, conf in pairs_sorted:
        ra, rb = find(a), find(b)
        if ra == rb:
            accepted.append((a, b, conf))
            continue
        # clique check: every cross pair must be an auto edge
        members_a = [k for k in all_keys if find(k) == ra]
        members_b = [k for k in all_keys if find(k) == rb]
        ok = True
        for u in members_a:
            for v in members_b:
                e = (min(u, v), max(u, v))
                if e not in edges:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            parent[ra] = rb
            accepted.append((a, b, conf))
        else:
            rejected.append((a, b, conf))
    return accepted, rejected


# ---------------- main ----------------
def estimate():
    t0 = time.time()
    nodes, cand_nodes = build_nodes()
    print(f"nodes: editions(movie)={len(nodes)}  collection-candidates={len(cand_nodes)}")

    # blocking
    keys = {}
    for n in nodes:
        for k in {n["title_canon"], n["base_wo_ord"]} | n["alias"]:
            if k:
                keys.setdefault(("ed", k), []).append(n["id"])
    by_id = {n["id"]: n for n in nodes}
    seen = set()
    pairs = []
    for (_, k), ids in keys.items():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = sorted((ids[i], ids[j]))
                if (a, b) not in seen:
                    seen.add((a, b))
                    pairs.append((a, b))
    # shared tconst
    tmap = {}
    for n in nodes:
        for t in n["tconsts"]:
            tmap.setdefault(t, []).append(n["id"])
    for ids in tmap.values():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = sorted((ids[i], ids[j]))
                if (a, b) not in seen:
                    seen.add((a, b))
                    pairs.append((a, b))
    # collection candidates (bucket B)
    cand_pairs = []
    for c in cand_nodes:
        for n in nodes:
            if c["title_canon"] and (c["title_canon"] == n["title_canon"]
                                     or c["title_canon"] in n["alias"]
                                     or jaccard(c["tokens"], n["tokens"]) >= 0.85):
                cand_pairs.append((c["id"], n["id"]))
    print(f"candidate pairs: edition×edition={len(pairs)}  collection-member×edition={len(cand_pairs)}")

    results = []
    for a, b in pairs:
        dec, reason, sig, V, conf, known, strong = decide(by_id[a], by_id[b], BASE)
        results.append({"a": a, "b": b, "decision": dec, "reason": reason, "V": round(V, 3),
                        "conf": round(conf, 3), "known": known, "strong": strong,
                        "signals": {k: round(v, 2) for k, v in sig.items()}})
    # relaxed variants (only counts)
    relax_counts = {}
    if sum(1 for r in results if r["decision"] == "manual_review") > 50:
        for name, th in (("V>=0.55", RELAX_A), ("known>=2", RELAX_B), ("both", RELAX_AB)):
            cnt = {"auto_merge": 0, "manual_review": 0, "no_merge": 0}
            for a, b in pairs:
                dec, *_ = decide(by_id[a], by_id[b], th)
                cnt[dec] += 1
            relax_counts[name] = cnt

    counts = {"auto_merge": 0, "manual_review": 0, "no_merge": 0}
    reason_break = {}
    for r in results:
        counts[r["decision"]] += 1
        reason_break[r["reason"]] = reason_break.get(r["reason"], 0) + 1

    auto_pairs = [(r["a"], r["b"], r["conf"]) for r in results if r["decision"] == "auto_merge"]
    acc, rej = chain_check(auto_pairs)
    cb_counts = {"auto_merge": 0, "manual_review": 0, "no_merge": 0}
    cb_samples = []
    cby = {c["id"]: c for c in cand_nodes}
    for cid, nid in cand_pairs:
        dec, reason, sig, V, conf, known, strong = decide(cby[cid], by_id[nid], BASE)
        cb_counts[dec] += 1
        if len(cb_samples) < 5:
            cb_samples.append({"pair": f"{cid} × {nid}", "decision": dec, "reason": reason, "conf": round(conf, 3)})

    samples = {}
    for dec in ("auto_merge", "manual_review", "no_merge"):
        rs = sorted([r for r in results if r["decision"] == dec],
                    key=lambda r: -r["conf"])[:5]
        samples[dec] = [{
            "pair": f"{r['a']} × {r['b']}",
            "titles": f"{by_id[r['a']]['title_raw']} × {by_id[r['b']]['title_raw']}",
            "conf": r["conf"], "V": r["V"], "known": r["known"], "strong": r["strong"],
            "reason": r["reason"], "signals": r["signals"],
        } for r in rs]

    out = {
        "rules_version": RULES_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_seconds": round(time.time() - t0, 1),
        "scope": {"movie_editions": len(nodes), "collection_candidates": len(cand_nodes),
                  "edition_pairs": len(pairs), "collection_pairs": len(cand_pairs)},
        "bucket_edition": {"counts": counts, "reason_breakdown": reason_break,
                           "chain_check": {"auto_edges": len(auto_pairs),
                                           "accepted_after_chain": len(acc),
                                           "rejected_by_chain": len(rej)},
                           "relaxed_variants": relax_counts},
        "bucket_collection_member": {"counts": cb_counts, "samples": cb_samples},
        "samples": samples,
        "thresholds_base": BASE,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "merge_estimate_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n==== 合并预估报告（edition×edition）====")
    print(f"候选对总数: {len(pairs)}")
    print(f"  auto_merge   : {counts['auto_merge']}")
    print(f"  manual_review: {counts['manual_review']}")
    print(f"  no_merge     : {counts['no_merge']}")
    print(f"链一致性预检: auto 边 {len(auto_pairs)} → 链合并后通过 {len(acc)}，拒边 {len(rej)}")
    print(f"原因分布: {reason_break}")
    if relax_counts:
        print(f"manual>50 → 放宽变体计数: {relax_counts}")
    print(f"合集成员对（独立桶）: {cb_counts}")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--estimate", action="store_true", help="合并预估报告（只算不写库）")
    args = ap.parse_args()
    if args.estimate:
        sys.exit(estimate())
    print("execution mode pending estimate confirmation — run with --estimate first")
    sys.exit(1)
