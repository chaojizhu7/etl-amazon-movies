# -*- coding: utf-8 -*-
"""Task 4: movie entity merge (rules/merge_rules_v1.md v1.1).

Mode --estimate: 合并预估报告（只算不写库）——候选对生成 → 6 信号验证 →
三级判定计数（auto_merge / manual_review / no_merge）→ 链一致性预检。
执行模式（写库）待预估报告人工确认后启用。

Usage (from project root):
    .venv\\Scripts\\python.exe src\\merge_movies.py --estimate
"""
import argparse
import csv
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
RULES_VERSION = "merge_v1.2"
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
        alias_raws = list(dict.fromkeys(
            [title_raw or ""] + (norm.get("alias_candidates") or []) + (norm.get("query_variants") or [])))
        trows = [by_tconst[t] for t in tconsts if t in by_tconst]
        # work-level 信号只采信已消歧/唯一候选的 tconst——多候选未消歧时取并集
        # 会把同名异片的演职员混入证据（来源冒充），故不采信 tconst 信号，仅留页面证据
        if res_t:
            sel = [r for r in trows if r["tconst"] == res_t]
        elif len(trows) == 1:
            sel = trows
        else:
            sel = []
        sig_tconsts = [r["tconst"] for r in sel]
        year = runtime_i = None
        genres_i = set()
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
        ambiguous = len(trows) > 1 and not res_t
        nodes.append({
            "kind": "edition", "id": asin, "title_raw": title_raw,
            "title_canon": canon(norm["base"]), "base_wo_ord": canon(base_wo),
            "alias": alias, "ord": ord_, "tconsts": tconsts, "resolved_t": res_t,
            "sig_tconsts": sig_tconsts, "ambiguous": ambiguous,
            "trows_by_id": {r["tconst"]: r for r in trows},
            "year": year, "year_src": "imdb" if year else None,
            "runtime": runtime_i if runtime_i else pg.get("runtime_min"),
            "genres": genres_i | tokens(canon(pg.get("genre") or "")),
            "dirs": name_sets([c["name"] for c in (pg.get("contributors") or []) if c.get("role") == "Director"]),
            "cast": name_sets([c["name"] for c in (pg.get("contributors") or []) if c.get("role") == "Actor"]),
            "multi_work": bool((pg.get("title_markers") or {}).get("multi_work_title")),
            "manual": ev.get("manual_review") or {},
            "tokens": tokens(canon(norm["base"])),
            "alias_raws": alias_raws,
        })
    # enrich director/cast from imdb cast cache（仅已消歧/唯一候选，防并集污染）
    if all_tconsts:
        cache = ensure_cast_cache(sorted(all_tconsts))
        for n in nodes:
            for t in (n.get("sig_tconsts") or []):
                c = cache.get(t) or {}
                n["dirs"] += name_sets(c.get("directors"))
                n["cast"] += name_sets(c.get("cast"))
            # 成对共享锚定用：每个候选 tconst 的独立证据（绝不并集跨片）
            cd = {}
            for t, row in (n.get("trows_by_id") or {}).items():
                c = cache.get(t) or {}
                try:
                    y = int(row.get("startYear")) if row.get("startYear") not in (None, "\\N") else None
                except ValueError:
                    y = None
                try:
                    rt = int(row.get("runtimeMinutes")) if row.get("runtimeMinutes") not in (None, "\\N") else None
                except ValueError:
                    rt = None
                g = set()
                for x in (row.get("genres") or "").split(","):
                    if x and x != "\\N":
                        g.update(tokens(canon(x)))
                cd[t] = {"year": y, "runtime": rt, "genres": g,
                         "dirs": name_sets(c.get("directors")), "cast": name_sets(c.get("cast"))}
            n["cand_data"] = cd

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
            "alias_raws": [ct],
        })
    return nodes, cand_nodes


# ---------------- signals (merge_rules §4) ----------------
def side_fields(n, anchor):
    """信号取数：已消歧/唯一候选 → 该作品证据；成对唯一共享 tconst → 锚定该作品；
    否则仅页面证据。绝不跨候选作品并集（铁律3 来源不冒充）。"""
    if n.get("sig_tconsts"):
        return {"year": n["year"], "runtime": n["runtime"], "genres": set(n["genres"]),
                "dirs": list(n["dirs"]), "cast": list(n["cast"])}
    if anchor and anchor in (n.get("cand_data") or {}):
        d = n["cand_data"][anchor]
        return {"year": d["year"], "runtime": d["runtime"] or n["runtime"],
                "genres": set(d["genres"]) | set(n["genres"]),
                "dirs": list(d["dirs"]) + list(n["dirs"]),
                "cast": list(d["cast"]) + list(n["cast"])}
    return {"year": None, "runtime": n["runtime"], "genres": set(n["genres"]),
            "dirs": list(n["dirs"]), "cast": list(n["cast"])}


def signals_for(a, b):
    sig, hard = {}, []
    shared = [t for t in a["tconsts"] if t in set(b["tconsts"])]
    anchor = shared[0] if len(shared) == 1 else None
    fa, fb = side_fields(a, anchor), side_fields(b, anchor)
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
    if fa["year"] and fb["year"]:
        d = abs(fa["year"] - fb["year"])
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
    if fa["dirs"] and fb["dirs"]:
        if overlap_names(fa["dirs"], fb["dirs"]):
            sig["director"] = 1.0
        else:
            sig["director"] = -1.0
    elif fa["dirs"] or fb["dirs"]:
        sig["director"] = 0.5
    else:
        sig["director"] = 0.0

    # S4 cast
    if fa["cast"] and fb["cast"]:
        ov = overlap_names(fa["cast"], fb["cast"])
        if ov >= 2:
            sig["cast"] = 1.0
        elif ov == 1:
            sig["cast"] = 0.5
        elif len(fa["cast"]) >= 3 and len(fb["cast"]) >= 3:
            sig["cast"] = -1.0
        else:
            sig["cast"] = -1.0
    else:
        sig["cast"] = 0.0

    # S5 runtime
    ra, rb = fa["runtime"], fb["runtime"]
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
    if fa["genres"] and fb["genres"]:
        jg = jaccard(fa["genres"], fb["genres"])
        if jg >= 0.5:
            sig["genre"] = 1.0
        elif jg > 0:
            sig["genre"] = 0.5
        elif len(fa["genres"]) >= 2 and len(fb["genres"]) >= 2:
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
        # v1.2: 高验证分的标题冲突疑同片别名 → 人工判，不直接 no_merge
        if V >= 0.80:
            return "manual_review", "title_conflict_high_v", sig, V, conf, known, strong
        return "no_merge", "title_conflict", sig, V, conf, known, strong
    if (T >= thresholds["T"] and V >= thresholds["V"] and strong >= thresholds["strong"]
            and known >= thresholds["known"]):
        # 未消歧多候选节点不进 auto（防同名异片误合，v1.3 ceiling）
        if a.get("ambiguous") or b.get("ambiguous"):
            return "manual_review", "ambiguous_node_ceiling", sig, V, conf, known, strong
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


# ---------------- pipeline ----------------
def run_pipeline():
    """候选生成 → 信号/判定（v1.2）→ 链一致性。返回全部中间结果（只算不写库）。"""
    t0 = time.time()
    nodes, cand_nodes = build_nodes()
    keys = {}
    for n in nodes:
        for k in {n["title_canon"], n["base_wo_ord"]} | n["alias"]:
            if k:
                keys.setdefault(("ed", k), []).append(n["id"])
    by_id = {n["id"]: n for n in nodes}
    seen, pairs = set(), []
    for (_, k), ids in keys.items():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = sorted((ids[i], ids[j]))
                if (a, b) not in seen:
                    seen.add((a, b))
                    pairs.append((a, b))
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
    cand_pairs = []
    for c in cand_nodes:
        for n in nodes:
            if c["title_canon"] and (c["title_canon"] == n["title_canon"]
                                     or c["title_canon"] in n["alias"]
                                     or jaccard(c["tokens"], n["tokens"]) >= 0.85):
                cand_pairs.append((c["id"], n["id"]))

    results = []
    for a, b in pairs:
        dec, reason, sig, V, conf, known, strong = decide(by_id[a], by_id[b], BASE)
        results.append({"a": a, "b": b, "decision": dec, "reason": reason, "V": round(V, 3),
                        "conf": round(conf, 3), "known": known, "strong": strong,
                        "signals": {k: round(v, 2) for k, v in sig.items()}})
    relax_counts = {}
    if sum(1 for r in results if r["decision"] == "manual_review") > 50:
        for name, th in (("V>=0.55", RELAX_A), ("known>=2", RELAX_B), ("both", RELAX_AB)):
            cnt = {"auto_merge": 0, "manual_review": 0, "no_merge": 0}
            for a, b in pairs:
                dec, *_ = decide(by_id[a], by_id[b], th)
                cnt[dec] += 1
            relax_counts[name] = cnt

    auto_pairs = [(r["a"], r["b"], r["conf"]) for r in results if r["decision"] == "auto_merge"]
    acc, rej = chain_check(auto_pairs)
    return {"nodes": nodes, "cand_nodes": cand_nodes, "by_id": by_id, "pairs": pairs,
            "cand_pairs": cand_pairs, "results": results, "acc": acc, "rej": rej,
            "relax": relax_counts, "elapsed": round(time.time() - t0, 1)}


def summarize(p):
    counts = {"auto_merge": 0, "manual_review": 0, "no_merge": 0}
    reason_break = {}
    for r in p["results"]:
        counts[r["decision"]] += 1
        reason_break[r["reason"]] = reason_break.get(r["reason"], 0) + 1
    return counts, reason_break


def estimate():
    p = run_pipeline()
    counts, reason_break = summarize(p)
    cb = {"auto_merge": 0, "manual_review": 0, "no_merge": 0}
    cby = {c["id"]: c for c in p["cand_nodes"]}
    for cid, nid in p["cand_pairs"]:
        dec, *_ = decide(cby[cid], p["by_id"][nid], BASE)
        cb[dec] += 1
    samples = {}
    for dec in ("auto_merge", "manual_review", "no_merge"):
        rs = sorted([r for r in p["results"] if r["decision"] == dec], key=lambda r: -r["conf"])[:5]
        samples[dec] = [{
            "pair": f"{r['a']} × {r['b']}",
            "titles": f"{p['by_id'][r['a']]['title_raw']} × {p['by_id'][r['b']]['title_raw']}",
            "conf": r["conf"], "V": r["V"], "known": r["known"], "strong": r["strong"],
            "reason": r["reason"], "signals": r["signals"],
        } for r in rs]
    out = {
        "rules_version": RULES_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_seconds": p["elapsed"],
        "scope": {"movie_editions": len(p["nodes"]), "collection_candidates": len(p["cand_nodes"]),
                  "edition_pairs": len(p["pairs"]), "collection_pairs": len(p["cand_pairs"])},
        "bucket_edition": {"counts": counts, "reason_breakdown": reason_break,
                           "chain_check": {"auto_edges": len(p["acc"]) + len(p["rej"]),
                                           "accepted_after_chain": len(p["acc"]),
                                           "rejected_by_chain": len(p["rej"])},
                           "relaxed_variants": p["relax"]},
        "bucket_collection_member": {"counts": cb},
        "samples": samples,
        "thresholds_base": BASE,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "merge_estimate_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("==== 合并预估报告（edition×edition）====")
    print(f"movie 产品 {len(p['nodes'])}，候选对 {len(p['pairs'])}")
    print(f"  auto_merge   : {counts['auto_merge']}")
    print(f"  manual_review: {counts['manual_review']}")
    print(f"  no_merge     : {counts['no_merge']}")
    print(f"链一致性: auto 边 {counts['auto_merge']} → 通过 {len(p['acc'])}，拒边 {len(p['rej'])}")
    print(f"原因分布: {reason_break}")
    if p["relax"]:
        print(f"manual>50 → 放宽变体: {p['relax']}")
    print(f"合集成员对: {cb}")
    print(f"wrote {out_path}")
    return out


# ---------------- execution ----------------
def build_clusters(acc_edges):
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in sorted(acc_edges, key=lambda e: (-e[2], e[0], e[1])):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    groups = {}
    for k in parent:
        groups.setdefault(find(k), []).append(k)
    return sorted((sorted(v) for v in groups.values()), key=lambda m: m[0])


def write_eval_sample(p, path):
    import random
    rng = random.Random(42)
    by_dec = {"auto_merge": [], "manual_review": [], "no_merge": []}
    for r in p["results"]:
        by_dec[r["decision"]].append(r)
    picked = []
    autos = sorted(by_dec["auto_merge"], key=lambda r: r["conf"])
    picked += autos[:12]                              # 边界低置信
    picked += by_dec["manual_review"]                 # 全部人工队列
    nomerge = by_dec["no_merge"]
    picked += rng.sample(nomerge, min(len(nomerge), max(0, 100 - len(picked) - 38)))
    chosen = {(r["a"], r["b"]) for r in picked}
    for r in autos:                                   # 补足 100（随机顺序已定）
        if len(picked) >= 100:
            break
        if (r["a"], r["b"]) not in chosen:
            picked.append(r)
            chosen.add((r["a"], r["b"]))
    picked = picked[:100]
    cols = ["pair_id", "node_a", "title_a", "node_b", "title_b", "system_decision",
            "reason", "V", "conf", "known_signals", "strong_signals", "signals_json",
            "human_verdict", "notes"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for i, r in enumerate(picked, 1):
            w.writerow([i, r["a"], p["by_id"][r["a"]]["title_raw"], r["b"],
                        p["by_id"][r["b"]]["title_raw"], r["decision"], r["reason"],
                        r["V"], r["conf"], r["known"], r["strong"],
                        json.dumps(r["signals"], ensure_ascii=False), "", ""])
    return len(picked)


def execute(run_id, expect_auto=125, tolerance=10):
    p = run_pipeline()
    counts, reason_break = summarize(p)
    auto_n = counts["auto_merge"]
    gate = {"expected_auto": expect_auto, "tolerance": tolerance, "actual_auto": auto_n,
            "delta": auto_n - expect_auto, "passed": abs(auto_n - expect_auto) <= tolerance}
    print(f"[gate] auto_merge {auto_n} vs 预估基线 {expect_auto} (±{tolerance}) → "
          f"delta={gate['delta']:+d}  {'PASS' if gate['passed'] else 'FAIL'}")
    if not gate["passed"]:
        diff_path = os.path.join(OUT_DIR, "merge_execute_gate_diff.json")
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(diff_path, "w", encoding="utf-8") as f:
            json.dump({"gate": gate, "counts": counts, "reason_breakdown": reason_break,
                       "note": "auto_merge 变化超出 ±阈值，按约定停止执行、等待人工确认"},
                      f, ensure_ascii=False, indent=2)
        print(f"变化较大，已写差异报告 {diff_path}，未执行合并")
        return 1

    # ---- clusters & movie_id（确定性：按成员元组排序编号）----
    clusters = build_clusters(p["acc"])
    merged = [c for c in clusters if len(c) >= 2]
    cluster_of = {}
    movie_ids = {}
    seq = 0
    def next_id():
        nonlocal seq
        seq += 1
        return f"mv_{seq:06d}"
    for members in merged:
        mid = next_id()
        movie_ids[mid] = {"members": members, "status": "merged"}
        for m in members:
            cluster_of[m] = mid
    for n in p["nodes"]:
        if n["id"] not in cluster_of:
            mid = next_id()
            movie_ids[mid] = {"members": [n["id"]], "status": "singleton"}
            cluster_of[n["id"]] = mid

    eval_path = os.path.join(OUT_DIR, "merge_eval_sample.csv")
    n_eval = write_eval_sample(p, eval_path)

    with psycopg.connect(**DB) as conn:
        with open(os.path.join(ROOT, "sql", "04_movie_merge.sql"), encoding="utf-8") as f:
            conn.execute(f.read())
        # 派生表全量重建（candidate/decision 为追加式历史，保留）
        conn.execute("DELETE FROM product_movie")
        conn.execute("DELETE FROM movie_alias")
        conn.execute("DELETE FROM movie_merge_cluster_log")
        conn.execute("DELETE FROM movie_entity")
        # candidate + decision（追加式）
        with conn.cursor() as cur:
            for r in p["results"]:
                cur.execute(
                    "INSERT INTO movie_merge_candidate "
                    "(node_a, node_b, node_kind, signals, title_coeff, score_v, score_conf, "
                    " known_signals, strong_signals, rules_version, run_id) "
                    "VALUES (%s,%s,'edition',%s::jsonb,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (node_a, node_b, run_id) DO NOTHING",
                    (r["a"], r["b"], json.dumps(r["signals"]), 1.0, r["V"], r["conf"],
                     r["known"], r["strong"], RULES_VERSION, run_id))
                cur.execute(
                    "INSERT INTO movie_merge_decision "
                    "(node_a, node_b, decision, reason, rules_version, run_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (r["a"], r["b"], r["decision"], r["reason"], RULES_VERSION, run_id))
        # movie_entity + alias
        for mid, info in movie_ids.items():
            members = info["members"]
            node = p["by_id"][members[0]]
            ext = sorted({t for m in members for t in p["by_id"][m]["tconsts"]})
            conn.execute(
                "INSERT INTO movie_entity (movie_id, title_canonical, external_ids, status, "
                "rules_version, run_id) VALUES (%s,%s,%s::jsonb,%s,%s,%s) "
                "ON CONFLICT (movie_id) DO UPDATE SET title_canonical=EXCLUDED.title_canonical, "
                "external_ids=EXCLUDED.external_ids, status=EXCLUDED.status, "
                "rules_version=EXCLUDED.rules_version, run_id=EXCLUDED.run_id, updated_at=now()",
                (mid, node["title_canon"], json.dumps({"imdb": ext}), info["status"],
                 RULES_VERSION, run_id))
            for m in members:
                n = p["by_id"][m]
                raws = list(dict.fromkeys(n.get("alias_raws") or [n["title_raw"] or ""]))
                for raw in raws[:12]:
                    if not raw:
                        continue
                    conn.execute(
                        "INSERT INTO movie_alias (movie_id, title_raw, title_canon, source, "
                        "source_locator, run_id) VALUES (%s,%s,%s,'product_page',%s,%s) "
                        "ON CONFLICT (movie_id, title_canon, source, source_locator) DO NOTHING",
                        (mid, raw, canon(raw), n["id"], run_id))
                conn.execute(
                    "INSERT INTO product_movie (asin, movie_id, relation_type, match_basis, "
                    "evidence, rules_version, run_id) VALUES (%s,%s,'single',%s,%s::jsonb,%s,%s) "
                    "ON CONFLICT (asin, movie_id) DO UPDATE SET match_basis=EXCLUDED.match_basis, "
                    "evidence=EXCLUDED.evidence, rules_version=EXCLUDED.rules_version, "
                    "run_id=EXCLUDED.run_id",
                    (n["id"], mid, "auto_merge_cluster" if info["status"] == "merged" else "singleton",
                     json.dumps({"tconsts": n["tconsts"][:5], "year": n["year"]}),
                     RULES_VERSION, run_id))
        # 合集成员映射（ProductMovie 多对多）
        coll = conn.execute("SELECT asin, movie_title_raw FROM product_movie_candidate").fetchall()
        canon_to_mid = {}
        for mid, info in movie_ids.items():
            for m in info["members"]:
                n = p["by_id"][m]
                canon_to_mid.setdefault(n["title_canon"], mid)
                for a in n["alias"]:
                    canon_to_mid.setdefault(a, mid)
        n_coll_linked = n_coll_unresolved = 0
        for asin, ct in coll:
            c = canon(ct)
            mid = canon_to_mid.get(c)
            if mid is None:
                mid = next_id()
                movie_ids[mid] = {"members": [], "status": "unresolved_candidate"}
                canon_to_mid[c] = mid
                conn.execute(
                    "INSERT INTO movie_entity (movie_id, title_canonical, external_ids, status, "
                    "rules_version, run_id) VALUES (%s,%s,'{}'::jsonb,'unresolved_candidate',%s,%s) "
                    "ON CONFLICT (movie_id) DO NOTHING",
                    (mid, c, RULES_VERSION, run_id))
                n_coll_unresolved += 1
            else:
                n_coll_linked += 1
            conn.execute(
                "INSERT INTO product_movie (asin, movie_id, relation_type, match_basis, "
                "evidence, rules_version, run_id) VALUES (%s,%s,'collection_member',%s,%s::jsonb,%s,%s) "
                "ON CONFLICT (asin, movie_id) DO NOTHING",
                (asin, mid, "candidate_title",
                 json.dumps({"movie_title_raw": ct, "title_canon": c}), RULES_VERSION, run_id))
        # cluster log
        rejected = [{"pair": [a, b], "conf": c} for a, b, c in p["rej"]]
        for mid, info in movie_ids.items():
            if info["status"] == "merged":
                conn.execute(
                    "INSERT INTO movie_merge_cluster_log (run_id, movie_id, members, cluster_size, "
                    "rejected_edges, notes) VALUES (%s,%s,%s::jsonb,%s,%s::jsonb,%s)",
                    (run_id, mid, json.dumps(info["members"]), len(info["members"]),
                     json.dumps(rejected), "chain clique ok" if not rejected else "见 rejected_edges"))
        conn.commit()

        after = {
            "movie_entity_total": conn.execute("SELECT count(*) FROM movie_entity").fetchone()[0],
            "merged_clusters": conn.execute(
                "SELECT count(*) FROM movie_entity WHERE status='merged'").fetchone()[0],
            "singletons": conn.execute(
                "SELECT count(*) FROM movie_entity WHERE status='singleton'").fetchone()[0],
            "unresolved_candidates": conn.execute(
                "SELECT count(*) FROM movie_entity WHERE status='unresolved_candidate'").fetchone()[0],
            "product_movie_single": conn.execute(
                "SELECT count(*) FROM product_movie WHERE relation_type='single'").fetchone()[0],
            "product_movie_collection": conn.execute(
                "SELECT count(*) FROM product_movie WHERE relation_type='collection_member'").fetchone()[0],
            "manual_review_queue": conn.execute(
                "SELECT count(*) FROM movie_merge_decision WHERE decision='manual_review' "
                "AND run_id=%s", (run_id,)).fetchone()[0],
            "no_merge": conn.execute(
                "SELECT count(*) FROM movie_merge_decision WHERE decision='no_merge' "
                "AND run_id=%s", (run_id,)).fetchone()[0],
        }

    # ---- 实例挑选 ----
    merged_examples = []
    for members in sorted(merged, key=lambda m: -len(m))[:5]:
        merged_examples.append({
            "movie_id": cluster_of[members[0]], "members": members,
            "titles": [p["by_id"][m]["title_raw"] for m in members],
            "tconsts": sorted({t for m in members for t in p["by_id"][m]["tconsts"]})[:3]})
    nomerge_examples = []
    seen_reason = set()
    for r in sorted(p["results"], key=lambda r: -r["conf"]):
        if r["decision"] in ("no_merge", "manual_review") and r["reason"] not in seen_reason:
            seen_reason.add(r["reason"])
            nomerge_examples.append({
                "pair": f"{r['a']} × {r['b']}",
                "titles": f"{p['by_id'][r['a']]['title_raw']} × {p['by_id'][r['b']]['title_raw']}",
                "decision": r["decision"], "reason": r["reason"], "V": r["V"], "conf": r["conf"],
                "signals": r["signals"]})
        if len(nomerge_examples) >= 5:
            break

    stats = {
        "rules_version": RULES_VERSION, "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gate": gate,
        "before": {"movie_editions": len(p["nodes"]), "candidate_pairs": len(p["pairs"]),
                   "distinct_title_canon": len({n["title_canon"] for n in p["nodes"]})},
        "decision_counts": counts, "reason_breakdown": reason_break,
        "chain_check": {"auto_edges": counts["auto_merge"],
                        "accepted_after_chain": len(p["acc"]), "rejected_by_chain": len(p["rej"])},
        "after": after,
        "collection_links": {"linked_to_existing_movie": n_coll_linked,
                             "unresolved_candidate": n_coll_unresolved},
        "eval_sample": {"file": eval_path, "n": n_eval},
        "merged_examples": merged_examples,
        "not_merged_examples": nomerge_examples,
    }
    stats_path = os.path.join(OUT_DIR, "merge_execution_stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print("==== 执行合并完成 ====")
    print(f"before: editions={stats['before']['movie_editions']} pairs={stats['before']['candidate_pairs']}")
    print(f"decisions: {counts}  chain: acc={len(p['acc'])} rej={len(p['rej'])}")
    print(f"after: {after}")
    print(f"eval sample: {n_eval} 对 → {eval_path}")
    print(f"wrote {stats_path}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--estimate", action="store_true", help="合并预估报告（只算不写库）")
    ap.add_argument("--execute", action="store_true", help="执行合并（写库，带 ±tolerance 门槛）")
    ap.add_argument("--run-id", default="run_20261104-01")
    ap.add_argument("--expect-auto", type=int, default=125, help="预估基线 auto_merge 对数")
    ap.add_argument("--tolerance", type=int, default=10)
    args = ap.parse_args()
    if args.estimate:
        sys.exit(0 if estimate() is not None else 1)
    if args.execute:
        sys.exit(execute(args.run_id, args.expect_auto, args.tolerance))
    print("usage: --estimate | --execute [--run-id ... --expect-auto N --tolerance N]")
    sys.exit(1)
