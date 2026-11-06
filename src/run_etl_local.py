# -*- coding: utf-8 -*-
"""ETL pipeline 本地运行器（与 etl/dags/movie_etl_dag.py 同构）。

任务链：extract → crawl → classify → merge → enrich → dedup → lineage → quality_check
- 幂等：成功 task 记录于 etl_run_task，重跑自动跳过；重跑不产生重复实体/关系
  （merge 重建时快照人工决策 → 重放 → 按成员集回写 movie_id，保证前后一致）
- 失败处置：写 quality_issue（issue_type=task_failed），支持 --inject-failure 演练
- 中断恢复：--resume 只补跑 failed/未跑 task

Usage:
  .venv\\Scripts\\python.exe src\\run_etl_local.py --run-id run_etl_demo_01 [--resume] [--inject-failure dedup]
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import psycopg

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
DB = dict(host="localhost", port="5432", dbname="etl_movie", user="postgres", password="postgres")
TASK_ORDER = ["extract", "crawl", "classify", "merge", "enrich", "dedup", "lineage", "quality_check"]
RULE_VERSIONS = {
    "extract": {"parser": "v0.1"},
    "crawl": {"rules": "crawl_priority_v0.1"},
    "classify": {"rules": "movie_classification_v0.1"},
    "merge": {"movie": "merge_v1.3", "person": "person_merge_v1.1"},
    "enrich": {"release": "release_v1.0", "field_sel": "field_sel_v1.0"},
    "dedup": {"rules": "review_dedup_v1.0"},
    "lineage": {"rules": "lineage_v1.0"},
    "quality_check": {"rules": "quality_v1.0"},
}
DEPENDENT_MOVIE_TABLES = [
    ("release_facts", "asin,fact_type,run_id"),
    ("field_assertion", "field_name,value_norm,value_role,source,run_id"),
    ("movie_person", "person_id,role,nconst,source"),
    ("movie_alias", "title_canon,source,source_locator"),
    ("product_movie", "asin,movie_id"),  # 特殊处理（主键含 movie_id）
]


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%SZ')}] {msg}", flush=True)


def run_cmd(cmd):
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = "\n".join((p.stdout or "").splitlines()[-8:])
    if p.returncode != 0:
        raise RuntimeError(f"cmd failed rc={p.returncode}: {' '.join(cmd)}\n{tail}\n{(p.stderr or '')[-500:]}")
    return tail


def task_state(conn, run_id, task_id):
    return conn.execute(
        "SELECT state, max(try_number) FROM etl_run_task WHERE run_id=%s AND task_id=%s "
        "GROUP BY state ORDER BY 2 DESC LIMIT 1", (run_id, task_id)).fetchone()


def move_table(conn, table, cols, keep, drop, preserve_conflicts=False):
    """迁移 drop→keep。撞 (cols) 键时：
    - 整行（除 id/movie_id）全同 → 删 drop 侧（真重复）；
    - 值不同且 preserve_conflicts=True（证据表）→ drop 侧 run_id 加 '#keep:<movie_id>'
      后缀保留双方，禁止静默丢证据；
    - 值不同且 preserve_conflicts=False（关系表）→ keep 侧胜出，删 drop 侧。"""
    key = " AND ".join(f't."{c.strip()}" IS NOT DISTINCT FROM s."{c.strip()}"' for c in cols.split(","))
    all_cols = [d.name for d in conn.execute(f"SELECT * FROM {table} WHERE false").description]
    val = [c for c in all_cols if c not in ("id", "movie_id")]
    val_eq = " AND ".join(f't."{c}" IS NOT DISTINCT FROM s."{c}"' for c in val)
    conn.execute(
        f"DELETE FROM {table} t USING {table} s WHERE t.movie_id=%s AND s.movie_id=%s "
        f"AND {key} AND {val_eq}", (drop, keep))
    if preserve_conflicts:
        conn.execute(
            f"UPDATE {table} t SET run_id = t.run_id || '#keep:' || t.movie_id "
            f"WHERE t.movie_id=%s AND t.run_id NOT LIKE '%%#keep:%%' AND EXISTS "
            f"(SELECT 1 FROM {table} s WHERE s.movie_id=%s AND {key})", (drop, keep))
    else:
        conn.execute(
            f"DELETE FROM {table} t USING {table} s WHERE t.movie_id=%s AND s.movie_id=%s AND {key}",
            (drop, keep))
    conn.execute(f"UPDATE {table} SET movie_id=%s WHERE movie_id=%s", (keep, drop))


def task_merge(run_id):
    """merge 步骤：快照既有簇+人工组锚定(movie_manual_group) → 重算 →
    组并集重放 → 精确重挂(asin 锚) → canonical id 回写。"""
    with psycopg.connect(**DB) as conn:
        old_clusters = {}
        for asin, mid in conn.execute(
                "SELECT asin, movie_id FROM product_movie WHERE relation_type='single'"):
            old_clusters.setdefault(mid, set()).add(asin)
        manual = {}
        for cid, a in conn.execute("SELECT canonical_movie_id, asin FROM movie_manual_group"):
            manual.setdefault(cid, set()).add(a)
        groups = [set(v) for v in old_clusters.values()] + list(manual.values())
        # 决策并集锚定：曾经 auto_merge/manual_merge 的对进并集（合并历史单调累积，
        # 后续重跑的系统判定不撤销已合并成果），仅人工后续 no_merge 可撤销；
        # 防止重建+中断后并集成果只活在状态快照里（快照丢=并集丢）
        for a, b in conn.execute(
                "SELECT DISTINCT LEAST(node_a,node_b), GREATEST(node_a,node_b) "
                "FROM movie_merge_decision d "
                "WHERE d.decision IN ('auto_merge','manual_merge') "
                "AND d.rules_version NOT LIKE '%v060%' AND d.rules_version NOT LIKE '%c45%' "          # 规则变更实验行不进并集（保证可回滚）
                "AND NOT EXISTS (SELECT 1 FROM movie_merge_decision r "
                " WHERE LEAST(r.node_a,r.node_b)=LEAST(d.node_a,d.node_b) "
                " AND GREATEST(r.node_a,r.node_b)=GREATEST(d.node_a,d.node_b) "
                " AND r.decision='no_merge' AND r.reason LIKE 'manual_confirm%' "
                " AND r.id > d.id)").fetchall():
            groups.append({a, b})
        merged_groups = []
        for g in groups:
            hit = [m for m in merged_groups if m & g]
            for h in hit:
                g = g | h
                merged_groups.remove(h)
            merged_groups.append(g)
        log(f"merge: snapshot clusters={len(old_clusters)}, manual groups={len(manual)}, "
            f"closure={len(merged_groups)}")
    run_cmd([PY, os.path.join("src", "merge_movies.py"), "--estimate"])
    est = json.load(open(os.path.join(ROOT, "logs", "summary", "merge_estimate_report.json"),
                         encoding="utf-8"))
    auto_n = est["bucket_edition"]["counts"]["auto_merge"]
    run_cmd([PY, os.path.join("src", "merge_movies.py"), "--execute",
             "--run-id", run_id, "--expect-auto", str(auto_n), "--tolerance", "5"])
    run_cmd([PY, os.path.join("src", "merge_persons.py"), "--execute", "--run-id", run_id])
    with psycopg.connect(**DB) as conn:
        cur_map = {}
        for asin, mid in conn.execute(
                "SELECT asin, movie_id FROM product_movie WHERE relation_type='single'"):
            cur_map.setdefault(asin, mid)
        # 1) 重放组并集（自动+人工成果）
        replayed = 0
        for g in merged_groups:
            ids = sorted({cur_map[a] for a in g if a in cur_map})
            if len(ids) <= 1:
                continue
            # 稳定 id：优先取成员集所属旧簇 id（重跑不换号，保 mv_ 引用稳定）；
            # 否则退回最小现 id（随后由 dance 对人工组回写 canonical）
            olds = sorted(o for o, asins in old_clusters.items() if asins <= g)
            primary = olds[0] if olds else ids[0]
            # 修复（2026-11-06）：primary∉ids 时旧 id 无实体行，UPDATE 会 0 行命中
            # → 补 INSERT（ON CONFLICT 置 merged），标题取首个 piece 实体
            src_t = None
            for cand in ids:
                if cand != primary:
                    r = conn.execute("SELECT title_canonical FROM movie_entity WHERE movie_id=%s", (cand,)).fetchone()
                    if r:
                        src_t = r[0]
                        break
            conn.execute(
                "INSERT INTO movie_entity (movie_id, title_canonical, external_ids, status, rules_version, run_id) "
                "VALUES (%s, %s, '{}'::jsonb, 'merged', 'merge_v1.3', %s) "
                "ON CONFLICT (movie_id) DO UPDATE SET status='merged'",
                (primary, src_t or primary, run_id))
            # 修复（2026-11-06）：primary 可能不在 ids 里，必须搬"所有 ≠primary 的 id"，
            # 否则 ids[0] 永远不搬导致人工组拆散（本次 32 组受损根因）
            for nid in ids:
                if nid == primary:
                    continue
                move_table(conn, "product_movie", "asin", primary, nid)
                for tbl, cols in DEPENDENT_MOVIE_TABLES:
                    if tbl != "product_movie":
                        move_table(conn, tbl, cols, primary, nid,
                                   preserve_conflicts=tbl in ("release_facts", "field_assertion"))
                conn.execute("DELETE FROM movie_entity WHERE movie_id=%s", (nid,))
                conn.execute("UPDATE movie_entity SET status='merged' WHERE movie_id=%s", (primary,))
                replayed += 1
        # 2) 精确重挂（有 asin 锚的行按 asin 当前归属；电影级行按旧簇归属）
        conn.execute(
            "UPDATE release_facts rf SET movie_id = pm.movie_id FROM product_movie pm "
            "WHERE pm.asin=rf.asin AND pm.relation_type='single' AND rf.asin<>'' AND rf.movie_id<>pm.movie_id")
        conn.execute(
            "UPDATE movie_alias ma SET movie_id = pm.movie_id FROM product_movie pm "
            "WHERE pm.asin=ma.source_locator AND pm.relation_type='single' AND ma.movie_id<>pm.movie_id")
        live = {r[0] for r in conn.execute("SELECT DISTINCT movie_id FROM product_movie")}
        stale_moved = 0
        stale_ids = [o for o in old_clusters if o not in live]
        log(f"merge: stale ids ({len(stale_ids)}): {stale_ids[:12]}")
        for old_id, asins in old_clusters.items():
            if old_id in live:
                continue                       # 修复①：活 id 不动（防 id 空间碰撞误伤）
            ps = sorted({cur_map[a] for a in asins if a in cur_map})
            if not ps:
                continue
            primary = ps[0]
            for tbl, cols in (("release_facts", "asin,fact_type,run_id"),
                              ("field_assertion", "field_name,value_norm,value_role,source,run_id"),
                              ("movie_person", "person_id,role,nconst,source"),
                              ("movie_alias", "title_canon,source,source_locator")):
                move_table(conn, tbl, cols, primary, old_id,
                           preserve_conflicts=tbl in ("release_facts", "field_assertion"))
            conn.execute("DELETE FROM movie_entity WHERE movie_id=%s", (old_id,))
            stale_moved += 1
        # 3) 人工组 canonical id 回写（占用者挪临时号；用重放后映射）
        cur_map = {}
        for asin, mid in conn.execute(
                "SELECT asin, movie_id FROM product_movie WHERE relation_type='single'"):
            cur_map.setdefault(asin, mid)
        renames = 0
        for cid, asins in manual.items():
            ps = sorted({cur_map[a] for a in asins if a in cur_map})
            if len(ps) != 1 or ps[0] == cid:
                continue
            mid = ps[0]
            occ = conn.execute("SELECT 1 FROM movie_entity WHERE movie_id=%s", (cid,)).fetchone()
            if occ:
                tmp = f"mv_hold_{cid[-6:]}"
                for tbl in ("movie_entity", "product_movie", "release_facts", "field_assertion",
                            "movie_person", "movie_alias"):
                    conn.execute(f"UPDATE {tbl} SET movie_id=%s WHERE movie_id=%s", (tmp, cid))
            for tbl in ("movie_entity", "product_movie", "release_facts", "field_assertion",
                        "movie_person", "movie_alias"):
                conn.execute(f"UPDATE {tbl} SET movie_id=%s WHERE movie_id=%s", (cid, mid))
            renames += 1
        # 4) mv_hold 影子 id 归位（canonical 占位迁移产物 → 正式 mv_ 编号；
        #    成员集签名复用保证后续重跑沿用新号不再回摆）
        hold_ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT movie_id FROM product_movie WHERE movie_id LIKE 'mv_hold%' ORDER BY 1")]
        if hold_ids:
            mx = conn.execute(
                "SELECT coalesce(max(substring(movie_id from 4))::int, 0) FROM movie_entity "
                "WHERE movie_id ~ '^mv_[0-9]{6}$'").fetchone()[0]
            for hid in hold_ids:
                mx += 1
                nid = f"mv_{mx:06d}"
                for tbl in ("movie_entity", "product_movie", "release_facts", "field_assertion",
                            "movie_person", "movie_alias"):
                    conn.execute(f"UPDATE {tbl} SET movie_id=%s WHERE movie_id=%s", (nid, hid))
            log(f"merge: mv_hold normalized {len(hold_ids)} -> mv_{mx-len(hold_ids)+1:06d}..mv_{mx:06d}")
        # 5) 人工清噪锚：movie_manual_split 的 (node_a × node_b) 不许同簇
        #    （传递吸收噪声防回摆；node_a=须拆出的噪声成员）
        n_desplit = 0
        for a, b in conn.execute("SELECT node_a, node_b FROM movie_manual_split").fetchall():
            rows = conn.execute(
                "SELECT asin, movie_id FROM product_movie "
                "WHERE asin IN (%s, %s) AND relation_type='single'", (a, b)).fetchall()
            mid_of = dict(rows)
            ma, mb = mid_of.get(a), mid_of.get(b)
            if not ma or not mb or ma != mb:
                continue
            mx = conn.execute(
                "SELECT coalesce(max(substring(movie_id from 4))::int, 0) FROM movie_entity "
                "WHERE movie_id ~ '^mv_[0-9]{6}$'").fetchone()[0]
            nid = f"mv_{mx+1:06d}"
            conn.execute(
                "INSERT INTO movie_entity (movie_id, title_canonical, external_ids, status, "
                "rules_version, run_id) SELECT %s, "
                "lower(regexp_replace(coalesce(pc.page_title_raw, %s), '[^a-zA-Z0-9 ]', ' ', 'g')), "
                "'{}'::jsonb, 'singleton', 'merge_v1.3', %s "
                "FROM product_classification pc WHERE pc.asin=%s "
                "ON CONFLICT (movie_id) DO NOTHING", (nid, a, run_id, a))
            for tbl in ("product_movie", "release_facts", "field_assertion", "movie_alias"):
                col = "source_locator" if tbl in ("movie_alias", "field_assertion") else "asin"
                conn.execute(
                    f"UPDATE {tbl} SET movie_id=%s WHERE {col}=%s AND movie_id=%s", (nid, a, ma))
            n_desplit += 1
            log(f"merge: de-split noise {a} x {b}: {ma} -> {nid}")
        if splits := conn.execute("SELECT count(*) FROM movie_manual_split").fetchone()[0]:
            log(f"merge: manual_split anchors={splits}, de-split fired={n_desplit}")
        # 6) 实体行自愈：merge_movies 重建会 DELETE 全表实体 → holding 与
        #    无 piece 实体的 pm 引用 id 补建（防实体悬空成孤儿）
        conn.execute(
            "INSERT INTO movie_entity (movie_id, title_canonical, external_ids, status, rules_version, run_id) "
            "VALUES ('mv_orphan_holding', 'orphaned evidence holding (thrash casualties)', '{}'::jsonb, "
            "'manual_hold', 'merge_v1.3', %s) ON CONFLICT (movie_id) DO NOTHING", (run_id,))
        n_heal = 0
        for (mid,) in conn.execute(
                "SELECT DISTINCT t.movie_id FROM product_movie t "
                "WHERE NOT EXISTS (SELECT 1 FROM movie_entity m WHERE m.movie_id=t.movie_id)").fetchall():
            conn.execute(
                "INSERT INTO movie_entity (movie_id, title_canonical, external_ids, status, rules_version, run_id) "
                "VALUES (%s, coalesce((SELECT lower(regexp_replace(coalesce(pc.page_title_raw, %s), "
                "'[^a-zA-Z0-9 ]', ' ', 'g')) FROM product_movie p "
                "JOIN product_classification pc ON pc.asin=p.asin "
                "WHERE p.movie_id=%s ORDER BY p.asin LIMIT 1), %s), '{}'::jsonb, "
                "(SELECT CASE WHEN count(*)>=2 THEN 'merged' ELSE 'singleton' END "
                "FROM product_movie WHERE movie_id=%s), 'merge_v1.3', %s) "
                "ON CONFLICT (movie_id) DO NOTHING", (mid, mid, mid, mid, mid, run_id))
            n_heal += 1
        if n_heal:
            log(f"merge: entity self-heal rebuilt={n_heal} + holding")
        conn.commit()
        total = conn.execute("SELECT count(*) FROM movie_entity").fetchone()[0]
        log(f"merge: done, movie_entity={total}, replayed={replayed}, stale_moved={stale_moved}, canonical_restored={renames}")


def exec_task(run_id, task_id):
    if task_id == "merge":
        task_merge(run_id)
        return {}
    cmds = {
        "extract": [PY, os.path.join("src", "stream_review_stats.py")],
        "crawl": [PY, os.path.join("src", "record_manifest.py"),
                  "--manifest", os.path.join("snapshots", "crawl_manifest.jsonl"),
                  "--run-id", f"{run_id}-crawl"],
        "classify": [PY, os.path.join("src", "classify_products.py"), "--run-id", run_id],
        "enrich": [PY, os.path.join("src", "complete_release_info.py"), "--run-id", run_id],
        "dedup": [PY, os.path.join("src", "clean_reviews.py"), "--run-id", run_id],
        "lineage": [PY, os.path.join("src", "build_lineage.py"), "--run-id", run_id],
        "quality_check": [PY, os.path.join("src", "quality_check.py"), "--run-id", run_id],
    }
    out = run_cmd(cmds[task_id])
    if task_id == "enrich":  # 任务6+7 合并为 enrich 步
        out += run_cmd([PY, os.path.join("src", "select_fields.py"), "--run-id", run_id])
    return {"tail": out[-300:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--force-task", default=None, help="强制重跑指定 task（逗号分隔）")
    ap.add_argument("--inject-failure", default=None,
                    help="演练：指定 task 第一次执行时注入失败")
    a = ap.parse_args()
    run_id, resume, inject = a.run_id, a.resume, a.inject_failure
    force = set((a.force_task or "").split(",")) - {""}

    with psycopg.connect(**DB) as conn:
        with open(os.path.join(ROOT, "sql", "10_etl_infra.sql"), encoding="utf-8") as f:
            conn.execute(f.read())
        conn.commit()

    for task_id in TASK_ORDER:
        with psycopg.connect(**DB) as conn:
            st = task_state(conn, run_id, task_id)
            done = st and st[0] == "success"
        if resume and done and task_id not in force:
            log(f"{task_id}: SKIP（已成功，不重做）")
            with psycopg.connect(**DB) as conn:
                conn.execute(
                    "INSERT INTO etl_run_task (run_id,task_id,state,try_number,rule_versions) "
                    "VALUES (%s,%s,'skipped',1,%s::jsonb) "
                    "ON CONFLICT (run_id,task_id,try_number) DO NOTHING",
                    (run_id, task_id, json.dumps(RULE_VERSIONS[task_id])))
                conn.commit()
            continue
        try_no = 1
        with psycopg.connect(**DB) as conn:
            row = conn.execute(
                "SELECT coalesce(max(try_number),0) FROM etl_run_task WHERE run_id=%s AND task_id=%s",
                (run_id, task_id)).fetchone()
            try_no = row[0] + 1
            conn.execute(
                "INSERT INTO etl_run_task (run_id,task_id,state,try_number,started_at,rule_versions) "
                "VALUES (%s,%s,'running',%s,now(),%s::jsonb)", (run_id, task_id, try_no,
                                                               json.dumps(RULE_VERSIONS[task_id])))
            conn.commit()
        log(f"{task_id}: RUN try#{try_no}")
        t0 = time.time()
        try:
            if inject == task_id and try_no == 1:
                raise RuntimeError(f"simulated failure injected at {task_id}")
            out = exec_task(run_id, task_id)
            with psycopg.connect(**DB) as conn:
                conn.execute(
                    "UPDATE etl_run_task SET state='success', ended_at=now(), output_ref=%s::jsonb "
                    "WHERE run_id=%s AND task_id=%s AND try_number=%s",
                    (json.dumps(out), run_id, task_id, try_no))
                conn.commit()
            log(f"{task_id}: OK {round(time.time()-t0,1)}s")
        except Exception as e:
            with psycopg.connect(**DB) as conn:
                conn.execute(
                    "UPDATE etl_run_task SET state='failed', ended_at=now(), error=%s "
                    "WHERE run_id=%s AND task_id=%s AND try_number=%s",
                    (str(e)[:500], run_id, task_id, try_no))
                conn.execute(
                    "INSERT INTO quality_issue (run_id,task_id,issue_type,detail,severity) "
                    "VALUES (%s,%s,'task_failed',%s,'error')", (run_id, task_id, str(e)[:500]))
                conn.commit()
            log(f"{task_id}: FAILED try#{try_no} -> {str(e)[:200]}")
            log("pipeline stopped（--resume 可补跑失败/未跑 task）")
            return 1
    log("pipeline complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
