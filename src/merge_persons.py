# -*- coding: utf-8 -*-
"""Task 5: person name normalization & disambiguation
(rules/person_merge_rules_v1.md v1.1).

直接执行模式：候选生成（姓名规范化仅作阻塞键）→ S1–S5 信号验证 → 三级判定
→ 链一致性 → person_id 分配 → 落库（person_entity/person_alias/
person_merge_candidate/decision/movie_person/person_cluster_log）
→ 100 对评价样本 + 5+5 实例 + 前后统计。

Usage (from project root):
    .venv\\Scripts\\python.exe src\\merge_persons.py --execute [--run-id run_20261104-04]
"""
import argparse
import csv
import gzip
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

import psycopg

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_DIR = os.path.join(ROOT, "logs", "summary")
IMDB_DIR = os.path.join(ROOT, "data", "raw", "imdb")
CACHE = os.path.join(OUT_DIR, "person_source_cache.json")
RULES_VERSION = "person_merge_v1.1"
DB = dict(host="localhost", port="5432", dbname="etl_movie", user="postgres", password="postgres")

W = {"works": 0.40, "era": 0.30, "profession": 0.15, "name": 0.15}
BASE = {"T": 0.9, "V": 0.70, "conf": 0.45, "strong_works": 1, "known": 2}
SUFFIX_RE = re.compile(r"\b(?:jr|sr|ii|iii|iv)\b\.?,?\s*$", re.I)


def name_canon(raw):
    """规范化姓名（仅候选生成用）：NFKD、去变音、大小写折叠、词序还原、去后缀。"""
    t = unicodedata.normalize("NFKD", raw or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.replace(".", " ").replace(",", " ")
    t = re.sub(r"\s+", " ", t).strip()
    if re.match(r"^\S+\s+\S", t) and " " not in t.split(" ", 1)[0]:
        pass
    # "Last First Middle" 已是常见形；"Last, First" 逗号已转空格
    t = SUFFIX_RE.sub("", t).strip()
    return t.casefold()


def name_parts(canon):
    toks = canon.split()
    if not toks:
        return "", ""
    return toks[0], toks[-1]


def initial_compatible(canon_a, canon_b):
    ta, tb = (canon_a or "").split(), (canon_b or "").split()
    if not ta or not tb or ta[-1] != tb[-1]:
        return False
    if len(ta) == 1 or len(tb) == 1:
        return True                       # 仅姓/单名提及 → 兼容（封顶 manual）
    return ta[0][0] == tb[0][0]


def name_signal(canon_a, canon_b):
    """返回 (s1, T)。缩写兼容 T=0.5 永不直接合并。"""
    if canon_a == canon_b:
        return 1.0, 1.0
    if initial_compatible(canon_a, canon_b):
        return 0.5, 0.5
    fa, la = name_parts(canon_a)
    fb, lb = name_parts(canon_b)
    if la == lb:
        return -1.0, 0.5                # 同姓不同名 → conflict
    return -1.0, 0.5


def load_title_by_tconst():
    """从 imdb_title_cache 反查 tconst → (startYear, genres)。"""
    by = {}
    path = os.path.join(OUT_DIR, "imdb_title_cache.json")
    if os.path.isfile(path):
        for rows in json.load(open(path, encoding="utf-8")).values():
            for r in rows:
                by.setdefault(r["tconst"], r)
    return by


def load_source_cache():
    if os.path.isfile(CACHE):
        try:
            return json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            pass
    return {}


def build_source_cache(need_tconsts, need_nconsts):
    """一次扫描 principals/crew/name.basics（带缓存）。"""
    cache = load_source_cache()
    cache.setdefault("principals", {})
    cache.setdefault("crew", {})
    cache.setdefault("names", {})
    miss_t = [t for t in need_tconsts if t not in cache["principals"]]
    if miss_t:
        need = set(miss_t)
        print(f"  scan title.principals for {len(need)} tconsts ...")
        with gzip.open(os.path.join(IMDB_DIR, "title.principals.tsv.gz"),
                       "rt", encoding="utf-8", errors="replace") as f:
            f.readline()
            for line in f:
                p = line.rstrip("\n").split("\t")
                if p[0] in need and len(p) >= 4:
                    try:
                        order = int(p[1])
                    except ValueError:
                        order = 99
                    cache["principals"].setdefault(p[0], []).append(
                        {"nconst": p[2], "category": p[3], "ordering": order})
        for t in need:
            cache["principals"].setdefault(t, [])
    miss_c = [t for t in need_tconsts if t not in cache["crew"]]
    if miss_c:
        need = set(miss_c)
        print(f"  scan title.crew for {len(need)} tconsts ...")
        with gzip.open(os.path.join(IMDB_DIR, "title.crew.tsv.gz"),
                       "rt", encoding="utf-8", errors="replace") as f:
            f.readline()
            for line in f:
                p = line.rstrip("\n").split("\t")
                if p[0] in need and len(p) >= 2:
                    cache["crew"][p[0]] = {
                        "directors": [x for x in p[1].split(",") if x and x != "\\N"]}
        for t in need:
            cache["crew"].setdefault(t, {"directors": []})
    miss_n = [n for n in need_nconsts if n not in cache["names"]]
    if miss_n:
        need = set(miss_n)
        print(f"  scan name.basics for {len(need)} nconsts ...")
        with gzip.open(os.path.join(IMDB_DIR, "name.basics.tsv.gz"),
                       "rt", encoding="utf-8", errors="replace") as f:
            f.readline()
            for line in f:
                p = line.rstrip("\n").split("\t")
                if p[0] in need and len(p) > 4:
                    cache["names"][p[0]] = {
                        "primaryName": p[1], "birthYear": p[2], "deathYear": p[3],
                        "primaryProfession": p[4]}
        for n in need:
            cache["names"].setdefault(n, {"primaryName": "", "birthYear": "\\N",
                                          "deathYear": "\\N", "primaryProfession": ""})
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    return cache


def build_nodes():
    with psycopg.connect(**DB) as conn:
        movies = {r[0]: r[1] for r in conn.execute(
            "SELECT movie_id, external_ids FROM movie_entity")}
        pm = {r[0]: r[1] for r in conn.execute(
            "SELECT asin, movie_id FROM product_movie WHERE relation_type='single'")}
        contrib = conn.execute(
            "SELECT asin, evidence->'page_signals'->'contributors' "
            "FROM product_classification WHERE evidence ? 'page_signals'").fetchall()

    t2m = {}
    for mid, ext in movies.items():
        for t in (ext or {}).get("imdb", []):
            t2m.setdefault(t, mid)
    tyears = load_title_by_tconst()
    need_t = sorted(t2m)

    # 页面贡献者（原名保留）→ (canon, movie_id) 聚合
    page_nodes = {}
    for asin, cs in contrib:
        mid = pm.get(asin)
        if not mid or not cs:
            continue
        for c in cs:
            nm = (c.get("name") or "").strip()
            if not nm:
                continue
            role = {"Director": "director", "Actor": "actor",
                    "Writer": "writer", "Producer": "producer"}.get(c.get("role"), "actor")
            if role not in ("director", "actor"):
                continue
            key = ("pg", name_canon(nm), mid)
            n = page_nodes.setdefault(key, {
                "key": f"pg:{name_canon(nm)[:40]}:{mid}", "kind": "page",
                "name_raws": [], "canon": name_canon(nm), "nconsts": set(),
                "works": set(), "roles": {}, "birth": None, "death": None,
                "professions": set()})
            n["name_raws"].append(nm)
            n["works"].add(mid)
            n["roles"].setdefault(mid, set()).add(role)

    # IMDb 侧：先扫 principals/crew 拿 nconst 集合
    pre_cache = load_source_cache()
    if not all(t in pre_cache.get("principals", {}) for t in need_t):
        build_source_cache(need_t, [])
    cache = load_source_cache()
    nconst_works = {}
    for t in need_t:
        mid = t2m.get(t)
        for row in cache["principals"].get(t, []):
            cat = row["category"]
            role = "actor" if cat in ("actor", "actress") else (
                "director" if cat == "director" else None)
            if not role:
                continue
            n = nconst_works.setdefault(row["nconst"], {"works": set(), "roles": {}})
            n["works"].add(mid)
            n["roles"].setdefault(mid, set()).add(role)
            n.setdefault("ordering", {})[mid] = row["ordering"]
        for nd in cache["crew"].get(t, {}).get("directors", []):
            n = nconst_works.setdefault(nd, {"works": set(), "roles": {}})
            n["works"].add(mid)
            n["roles"].setdefault(mid, set()).add("director")

    build_source_cache(need_t, sorted(nconst_works))
    cache = load_source_cache()

    nodes = {}
    for nc, info in nconst_works.items():
        nm = cache["names"].get(nc, {})
        raw = nm.get("primaryName") or nc
        try:
            birth = int(nm.get("birthYear")) if nm.get("birthYear") not in (None, "\\N") else None
        except ValueError:
            birth = None
        try:
            death = int(nm.get("deathYear")) if nm.get("deathYear") not in (None, "\\N") else None
        except ValueError:
            death = None
        profs = {p.strip() for p in (nm.get("primaryProfession") or "").split(",") if p.strip()}
        wy = []
        for t, mid in t2m.items():
            if mid in info["works"]:
                y = (tyears.get(t) or {}).get("startYear")
                if y and y != "\\N":
                    try:
                        wy.append(int(y))
                    except ValueError:
                        pass
        nodes[nc] = {
            "key": nc, "kind": "imdb", "name_raws": [raw], "canon": name_canon(raw),
            "nconsts": {nc}, "works": set(info["works"]), "roles": info["roles"],
            "birth": birth, "death": death, "professions": profs,
            "work_years": (min(wy), max(wy)) if wy else None,
            "ordering": info.get("ordering", {}),
        }
    for key, n in page_nodes.items():
        nodes[n["key"]] = n
    return nodes, t2m


# ---------------- era (v1.1) ----------------
def era_info(n):
    wy = n.get("work_years") or (None, None)
    nworks = len(n["works"])
    if n.get("birth") is not None:
        end = n.get("death") if n.get("death") is not None else 2026
        start = n["birth"]
        if wy[0]:
            start = min(start, wy[0])
            end = max(end, wy[1] or end)
        return {"start": start, "end": end, "basis": "lifespan",
                "inferred": False, "confidence": 0.95, "n_works": nworks}
    if wy[0]:
        conf = 0.8 if nworks >= 5 else (0.6 if nworks >= 3 else 0.4)
        return {"start": wy[0], "end": wy[1], "basis": "work_years",
                "inferred": True, "confidence": conf, "n_works": nworks}
    return {"start": None, "end": None, "basis": "unknown",
            "inferred": True, "confidence": 0.0, "n_works": nworks}


def era_signal(a, b):
    ea, eb = era_info(a), era_info(b)
    if ea["start"] is None or eb["start"] is None:
        return 0.0, None, ea, eb
    # 区间：[start, end]，在世者 end=2026
    a0, a1 = ea["start"], ea["end"]
    b0, b1 = eb["start"], eb["end"]
    if a1 >= b0 and b1 >= a0:
        return 1.0, "overlap", ea, eb
    gap = max(a0, b0) - min(a1, b1)
    if gap <= 5:
        return 0.5, f"gap_{gap}", ea, eb
    return -1.0, f"gap_{gap}", ea, eb


# ---------------- signals & decision ----------------
def signals_for(a, b):
    sig, hard = {}, []
    s1, T = name_signal(a["canon"], b["canon"])
    sig["name"] = s1
    shared_works = a["works"] & b["works"]
    if len(shared_works) >= 2:
        sig["works"] = 1.0
    elif len(shared_works) == 1:
        sig["works"] = 0.5
    elif len(a["works"]) >= 2 and len(b["works"]) >= 2:
        sig["works"] = -1.0
    else:
        sig["works"] = 0.0

    def roles_of(n):
        s = set()
        for rs in n.get("roles", {}).values():
            s |= set(rs)
        s |= {("actor" if "actress" in p else p) for p in n.get("professions", set())
              if p in ("actor", "actress", "director")}
        return s

    ra, rb = roles_of(a), roles_of(b)
    if ra and rb:
        if ra & rb:
            sig["profession"] = 1.0
        elif sig["works"] == -1.0:
            sig["profession"] = -1.0
        else:
            sig["profession"] = 0.5
    else:
        sig["profession"] = 0.5

    s4, gap_tag, ea, eb = era_signal(a, b)
    sig["era"] = s4
    era_sparse = False
    if gap_tag and gap_tag.startswith("gap_"):
        g = int(gap_tag.split("_")[1])
        if g > 5:
            if ea["n_works"] >= 5 and eb["n_works"] >= 5:
                hard.append("era_disjoint")
            else:
                era_sparse = True                # v1.1：稀疏不判死 → manual
    # deathYear 矛盾
    for x, y in ((a, b), (b, a)):
        if x.get("death") and y.get("work_years") and y["work_years"][0]:
            if x["death"] < y["work_years"][0]:
                hard.append("death_before_other_works")
    # 互斥代表作（H2）：0 交集 + 各≥2 + 时期重叠
    if sig["works"] == -1.0 and gap_tag == "overlap":
        hard.append("exclusive_works")

    known = [k for k in ("works", "era", "profession", "name") if sig[k] != 0.0]
    V = (sum(W[k] * sig[k] for k in known) / sum(W[k] for k in known)) if known else 0.0
    conf = T * (0.30 + 0.70 * max(V, 0.0))
    return sig, hard, V, conf, T, known, era_sparse, ea, eb, shared_works


def decide(a, b):
    sig, hard, V, conf, T, known, era_sparse, ea, eb, shared = signals_for(a, b)
    if a["nconsts"] & b["nconsts"]:
        return "auto_merge", "shared_nconst", sig, V, conf, known
    if hard:
        return "no_merge", "hard:" + "+".join(sorted(set(hard))), sig, V, conf, known
    if era_sparse:
        return "manual_review", "era_gap_sparse", sig, V, conf, known
    if sig["name"] == -1.0 and not (a["canon"] == b["canon"]):
        return "no_merge", "name_conflict", sig, V, conf, known
    if T <= 0.5 and sig["name"] == 0.5:
        pass                                   # 缩写兼容：封顶 manual（下方处理）
    else:
        if (T >= BASE["T"] and len(shared) >= BASE["strong_works"]
                and V >= BASE["V"] and len(known) >= BASE["known"]):
            return "auto_merge", "all_gates_pass", sig, V, conf, known
    if conf < BASE["conf"]:
        return "no_merge", "conf_below_floor", sig, V, conf, known
    if sig["name"] == 0.5:
        return "manual_review", "initial_compatible_ceiling", sig, V, conf, known
    if len(shared) == 0 and len(known) < BASE["known"]:
        return "manual_review", "known_signals_lt_2", sig, V, conf, known
    return "manual_review", "partial_signals", sig, V, conf, known


def chain_check(auto_pairs):
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
        members_a = [k for k in all_keys if find(k) == ra]
        members_b = [k for k in all_keys if find(k) == rb]
        ok = all((min(u, v), max(u, v)) in edges for u in members_a for v in members_b)
        if ok:
            parent[ra] = rb
            accepted.append((a, b, conf))
        else:
            rejected.append((a, b, conf))
    return accepted, rejected


def execute(run_id):
    t0 = time.time()
    nodes, t2m = build_nodes()
    print(f"nodes: total={len(nodes)}  imdb={sum(1 for n in nodes.values() if n['kind']=='imdb')}  "
          f"page={sum(1 for n in nodes.values() if n['kind']=='page')}")

    # blocking：K1 canon / K2 姓+名首字母 / K3 nconst
    keys = {}
    for n in nodes.values():
        fa, la = name_parts(n["canon"])
        kk = {("canon", n["canon"]), ("k2", f"{la}|{fa[:1] if fa else ''}")}
        for t in n["nconsts"]:
            kk.add(("nc", t))
        for k in kk:
            if k[1]:
                keys.setdefault(k, []).append(n["key"])
    seen, pairs = set(), []
    for _, ids in keys.items():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = sorted((ids[i], ids[j]))
                if (a, b) not in seen:
                    seen.add((a, b))
                    pairs.append((a, b))
    print(f"candidate pairs: {len(pairs)}")

    results = []
    for a, b in pairs:
        dec, reason, sig, V, conf, known = decide(nodes[a], nodes[b])
        results.append({"a": a, "b": b, "decision": dec, "reason": reason,
                        "V": round(V, 3), "conf": round(conf, 3), "known": len(known),
                        "signals": {k: round(v, 2) for k, v in sig.items()}})
    counts = {"auto_merge": 0, "manual_review": 0, "no_merge": 0}
    rb = {}
    for r in results:
        counts[r["decision"]] += 1
        rb[r["reason"]] = rb.get(r["reason"], 0) + 1
    auto_pairs = [(r["a"], r["b"], r["conf"]) for r in results if r["decision"] == "auto_merge"]
    acc, rej = chain_check(auto_pairs)

    # clusters
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in acc:
        ra, rb2 = find(a), find(b)
        if ra != rb2:
            parent[ra] = rb2
    groups = {}
    for k in {x for p in acc for x in p[:2]}:
        groups.setdefault(find(k), []).append(k)
    clusters = sorted((sorted(v) for v in groups.values()), key=lambda m: m[0])
    assigned = {}
    cluster_members = {}
    # 成员集签名 pid 复用：节点集未变的人物簇保留原 person_id（重跑不重铸，保引用稳定）
    old_sig = {}
    with psycopg.connect(**DB) as conn:
        for pid, loc in conn.execute("SELECT person_id, source_locator FROM person_alias"):
            if loc:
                old_sig.setdefault(pid, set()).add(loc)
    sig_to_id = {frozenset(v): k for k, v in old_sig.items()}
    taken = set()
    seq = max((int(x[2:]) for x in old_sig if x.startswith("p_") and x[2:].isdigit()), default=0)
    def mint_pid(members):
        nonlocal seq
        old = sig_to_id.get(frozenset(members))
        if old and old not in taken:
            taken.add(old)
            return old
        while True:
            seq += 1
            cand = f"p_{seq:06d}"
            if cand not in old_sig and cand not in taken:
                taken.add(cand)
                return cand
    for members in clusters:
        pid = mint_pid(members)
        cluster_members[pid] = members
        for m in members:
            assigned[m] = pid
    for n in nodes.values():
        if n["key"] not in assigned:
            pid = mint_pid([n["key"]])
            cluster_members[pid] = [n["key"]]
            assigned[n["key"]] = pid

    # eval sample
    rng_seed = 42
    import random
    rng = random.Random(rng_seed)
    by_dec = {"auto_merge": [], "manual_review": [], "no_merge": []}
    for r in results:
        by_dec[r["decision"]].append(r)
    picked = []
    autos = sorted(by_dec["auto_merge"], key=lambda r: r["conf"])
    picked += autos[:12]
    picked += by_dec["manual_review"][:20]
    picked += rng.sample(by_dec["no_merge"], min(len(by_dec["no_merge"]), 40))
    chosen = {(r["a"], r["b"]) for r in picked}
    for r in autos:
        if len(picked) >= 100:
            break
        if (r["a"], r["b"]) not in chosen:
            picked.append(r)
            chosen.add((r["a"], r["b"]))
    for r in by_dec["manual_review"]:
        if len(picked) >= 100:
            break
        if (r["a"], r["b"]) not in chosen:
            picked.append(r)
            chosen.add((r["a"], r["b"]))
    picked = picked[:100]
    eval_path = os.path.join(OUT_DIR, "person_eval_sample.csv")
    _old = []
    if os.path.exists(eval_path):                 # 防覆盖：已有人工标注的评价样本不重生成
        with open(eval_path, encoding="utf-8") as _f:
            _old = list(csv.DictReader(_f))
    if any((r.get("human_verdict") or "").strip() for r in _old):
        print("[eval] person_eval_sample.csv 含人工标注，跳过重生成")
    else:
        with open(eval_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["pair_id", "node_a", "name_a", "node_b", "name_b", "system_decision",
                        "reason", "V", "conf", "signals_json", "human_verdict", "notes"])
            for i, r in enumerate(picked, 1):
                w.writerow([i, r["a"], nodes[r["a"]]["canon"], r["b"], nodes[r["b"]]["canon"],
                            r["decision"], r["reason"], r["V"], r["conf"],
                            json.dumps(r["signals"], ensure_ascii=False), "", ""])

    # ---- 落库 ----
    with psycopg.connect(**DB) as conn:
        with open(os.path.join(ROOT, "sql", "05_person_merge.sql"), encoding="utf-8") as f:
            conn.execute(f.read())
        conn.execute("DELETE FROM movie_person")
        conn.execute("DELETE FROM person_alias")
        conn.execute("DELETE FROM person_cluster_log")
        conn.execute("DELETE FROM person_entity")
        with conn.cursor() as cur:
            for r in results:
                cur.execute(
                    "INSERT INTO person_merge_candidate "
                    "(node_a,node_b,signals,title_coeff,score_v,score_conf,known_signals,"
                    "rules_version,run_id) VALUES (%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (node_a,node_b,run_id) DO NOTHING",
                    (r["a"], r["b"], json.dumps(r["signals"]), 1.0, r["V"], r["conf"],
                     r["known"], RULES_VERSION, run_id))
                cur.execute(
                    "INSERT INTO person_merge_decision "
                    "(node_a,node_b,decision,reason,rules_version,run_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (r["a"], r["b"], r["decision"], r["reason"], RULES_VERSION, run_id))
        for pid, members in cluster_members.items():
            ns = [nodes[m] for m in members]
            canon = ns[0]["canon"]
            nconsts = sorted({c for n in ns for c in n["nconsts"]})
            profs = sorted({p for n in ns for p in n.get("professions", set())})[:4]
            era = era_info(ns[0]) if ns[0]["kind"] == "imdb" else era_info(ns[0])
            for n in ns[1:]:
                e2 = era_info(n)
                if era["start"] is None and e2["start"] is not None:
                    era = e2
            status = "merged" if len(members) >= 2 else (
                "pending_review" if len(ns[0]["works"]) == 0 and ns[0]["kind"] == "page" else "singleton")
            conn.execute(
                "INSERT INTO person_entity (person_id,name_canonical,external_ids,"
                "primary_occupation,era_span,status,rules_version,run_id) "
                "VALUES (%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s,%s)",
                (pid, canon, json.dumps({"imdb": nconsts}), ",".join(profs),
                 json.dumps({k: era[k] for k in ("start", "end", "basis", "inferred", "confidence")}),
                 status, RULES_VERSION, run_id))
            for n in ns:
                for raw in dict.fromkeys(n["name_raws"]):
                    conn.execute(
                        "INSERT INTO person_alias (person_id,name_raw,name_canon,source,"
                        "source_locator,run_id) VALUES (%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (person_id,name_canon,source,source_locator) DO NOTHING",
                        (pid, raw, name_canon(raw),
                         "imdb" if n["kind"] == "imdb" else "product_page",
                         n["key"], run_id))
                # movie_person
                for mid, roles in n.get("roles", {}).items():
                    for role in sorted(roles):
                        if role not in ("director", "actor", "actress"):
                            continue
                        conn.execute(
                            "INSERT INTO movie_person (movie_id,person_id,role,ordering,"
                            "nconst,source,source_locator,evidence,rules_version,run_id) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) "
                            "ON CONFLICT (movie_id,person_id,role,nconst,source) DO NOTHING",
                            (mid, pid, role,
                             (n.get("ordering") or {}).get(mid),
                             list(n["nconsts"])[0] if n["kind"] == "imdb" else "",
                             "imdb" if n["kind"] == "imdb" else "product_page",
                             n["key"], json.dumps({"name_raws": n["name_raws"][:3]}),
                             RULES_VERSION, run_id))
            if len(members) >= 2:
                conn.execute(
                    "INSERT INTO person_cluster_log (run_id,person_id,members,cluster_size,"
                    "rejected_edges,notes) VALUES (%s,%s,%s,%s,%s::jsonb,%s)",
                    (run_id, pid, json.dumps([nodes[m]["key"] for m in members]),
                     len(members), json.dumps([{"pair": [a, b]} for a, b, _ in rej]),
                     "auto cluster, chain ok" if not rej else "见 rejected_edges"))
        conn.commit()
        after = dict(conn.execute(
            "SELECT status, count(*) FROM person_entity GROUP BY 1").fetchall())
        mp = conn.execute("SELECT count(*) FROM movie_person").fetchone()[0]

    # ---- 5+5 实例 ----
    merged_examples = []
    for pid, members in sorted(cluster_members.items(),
                               key=lambda kv: -len(kv[1])):
        if len(members) < 2 or len(merged_examples) >= 5:
            continue
        ns = [nodes[m] for m in members]
        merged_examples.append({
            "person_id": pid,
            "members": [n["key"] for n in ns],
            "names": list(dict.fromkeys(r for n in ns for r in n["name_raws"]))[:4],
            "nconsts": sorted({c for n in ns for c in n["nconsts"]}),
            "shared_works_evidence": sorted(set.intersection(*[n["works"] for n in ns]))[:4]
            if all(n["works"] for n in ns) else [],
            "era": [era_info(n) for n in ns][:2]})
    non_examples = []
    seen_reason = set()
    for r in sorted(results, key=lambda r: -r["conf"]):
        if r["decision"] in ("no_merge", "manual_review") and r["reason"] not in seen_reason:
            seen_reason.add(r["reason"])
            non_examples.append({
                "pair": f"{nodes[r['a']]['canon']} × {nodes[r['b']]['canon']}",
                "keys": [r["a"], r["b"]], "decision": r["decision"], "reason": r["reason"],
                "V": r["V"], "conf": r["conf"], "signals": r["signals"],
                "works": [sorted(nodes[r["a"]]["works"])[:3], sorted(nodes[r["b"]]["works"])[:3]],
                "era": [era_info(nodes[r["a"]]), era_info(nodes[r["b"]])]})
        if len(non_examples) >= 5:
            break

    stats = {
        "rules_version": RULES_VERSION, "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_seconds": round(time.time() - t0, 1),
        "before": {"nodes_imdb": sum(1 for n in nodes.values() if n["kind"] == "imdb"),
                   "nodes_page": sum(1 for n in nodes.values() if n["kind"] == "page"),
                   "candidate_pairs": len(pairs)},
        "decision_counts": counts, "reason_breakdown": rb,
        "chain_check": {"auto_edges": len(auto_pairs), "accepted": len(acc), "rejected": len(rej)},
        "after": {"person_entity_by_status": after, "person_entity_total": sum(after.values()),
                  "merged_clusters": sum(1 for m in cluster_members.values() if len(m) >= 2),
                  "movie_person_rows": mp},
        "eval_sample": {"file": eval_path, "n": len(picked)},
        "merged_examples": merged_examples,
        "not_merged_examples": non_examples,
    }
    spath = os.path.join(OUT_DIR, "person_merge_stats.json")
    json.dump(stats, open(spath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("==== 人物合并执行完成 ====")
    print(f"decisions: {counts}  reasons: {rb}")
    print(f"chain: acc={len(acc)} rej={len(rej)}")
    print(f"after: {after}  movie_person={mp}")
    print(f"eval sample: {len(picked)} 对 → {eval_path}")
    print(f"wrote {spath}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--run-id", default="run_20261104-04")
    args = ap.parse_args()
    if args.execute:
        sys.exit(execute(args.run_id))
    print("usage: --execute [--run-id ...]")
    sys.exit(1)
