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


def move_table(conn, table, cols, keep, drop):
    conn.execute(
        f"DELETE FROM {table} WHERE movie_id=%s AND ({cols}) IN "
        f"(SELECT {cols} FROM {table} WHERE movie_id=%s)", (drop, keep))
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
            primary = ids[0]
            for nid in ids[1:]:
                move_table(conn, "product_movie", "asin", primary, nid)
                for tbl, cols in DEPENDENT_MOVIE_TABLES:
                    if tbl != "product_movie":
                        move_table(conn, tbl, cols, primary, nid)
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
                move_table(conn, tbl, cols, primary, old_id)
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
