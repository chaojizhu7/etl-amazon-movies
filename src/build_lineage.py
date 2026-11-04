# -*- coding: utf-8 -*-
"""Task 9: 字段级数据血缘加载器——从既有表装配血缘层（sql/09_lineage.sql）。

来源映射：field_assertion / release_facts / movie_alias / person_alias / movie_person
→ lineage_field_assertion（每候选值一行，未采用保留）；最终值 → lineage_field_final；
合并/选择/人工确认 → lineage_process_step；源记录 → lineage_source_record。

Usage: .venv\\Scripts\\python.exe src\\build_lineage.py [--run-id run_20261104-11]
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

import psycopg

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB = dict(host="localhost", port="5432", dbname="etl_movie", user="postgres", password="postgres")


def sha256_file(path, limit=None):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            if limit:
                h.update(f.read(limit))
            else:
                for ch in iter(lambda: f.read(1 << 20), b""):
                    h.update(ch)
        return h.hexdigest()
    except OSError:
        return None


def main(run_id):
    t0 = time.time()
    with psycopg.connect(**DB) as conn:
        with open(os.path.join(ROOT, "sql", "09_lineage.sql"), encoding="utf-8") as f:
            conn.execute(f.read())
        for t in ("lineage_field_final", "lineage_process_step",
                  "lineage_field_assertion", "lineage_source_record"):
            conn.execute(f"DELETE FROM {t}")

        # ---- 源记录 ----
        src_rows = [
            ("sr_file_movies_txt", "review_file", "data/raw/movies.txt.gz",
             "4b6ee09ea4880f3fb197b1407d45dfb08a892fbb48a9e7a2d74aa0572cdcc715",
             "records 1..7911684", "2026-11-03T00:00:00Z"),
            ("sr_manual_review", "manual_review", "report/人工复核记录.md",
             sha256_file(os.path.join(ROOT, "report", "人工复核记录.md")),
             "第一~三轮人工复核", "2026-11-04T00:00:00Z"),
            ("sr_derived_system", "derived_system", "system:derived",
             None, "inferred_release_bound 等", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ]
        # IMDb 数据集（checksums.sha256）
        ck = {}
        ck_path = os.path.join(ROOT, "data", "raw", "imdb", "checksums.sha256")
        if os.path.isfile(ck_path):
            for line in open(ck_path, encoding="utf-8"):
                parts = line.split()
                if len(parts) >= 2:
                    ck[os.path.basename(parts[-1])] = parts[0]
        for ds in ("title.basics.tsv.gz", "title.crew.tsv.gz",
                   "title.principals.tsv.gz", "name.basics.tsv.gz", "title.ratings.tsv.gz"):
            key = f"sr_imdb_{ds.split('.')[0]}_{ds.split('.')[1]}"
            src_rows.append((key, "imdb_dataset", f"data/raw/imdb/{ds}",
                             ck.get(ds), "IMDb non-commercial dataset", "2026-11-03T00:00:00Z"))
        # 产品页快照
        snap = conn.execute(
            "SELECT asin, snapshot_sha256 FROM product_classification "
            "WHERE snapshot_sha256 IS NOT NULL").fetchall()
        for asin, ssha in snap:
            src_rows.append((f"sr_page_{asin}", "product_page_snapshot",
                             f"snapshots/pages/{asin}.html.gz", ssha, asin,
                             "2026-11-03T00:00:00Z"))
        with conn.cursor() as cur:
            for r in src_rows:
                cur.execute(
                    "INSERT INTO lineage_source_record (source_record_id,source_type,source_address,"
                    "snapshot_sha256,raw_locator,acquired_at) VALUES (%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (source_record_id) DO NOTHING", r)

        def sr_for(source, locator, field):
            if source == "product_page":
                cand = f"sr_page_{locator}" if locator else None
                return cand if cand in known_sr else "sr_derived_system"
            if source == "imdb":
                if field == "name":
                    return "sr_imdb_name_basics"
                if field == "starring":
                    return "sr_imdb_title_principals"
                if field in ("directors",):
                    return "sr_imdb_title_crew"
                if field in ("person_credit", "role_credit"):
                    return "sr_imdb_title_principals"
                return "sr_imdb_title_basics"
            if source == "review":
                return "sr_file_movies_txt"
            if source == "manual":
                return "sr_manual_review"
            return "sr_derived_system"

        known_sr = {r[0] for r in src_rows}
        ins = []

        def add_assert(et, eid, f, raw, norm, role, source, loc, rule, ver, conf, status):
            ins.append((et, eid, f, role, raw, norm, source,
                        sr_for(source, loc, f), loc, rule, ver, conf, status))

        # field_assertion（任务7）
        for r in conn.execute(
                "SELECT movie_id, field_name, value_raw, value_norm, value_role, source, "
                "source_locator, match_rule, rules_version, confidence, status "
                "FROM field_assertion"):
            add_assert("movie", r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10])
        # release_facts（任务6）
        for r in conn.execute(
                "SELECT movie_id, asin, fact_type, value_raw, value_norm, source, source_locator, "
                "match_rule, 'release_v1.0' AS rules_version, confidence, status FROM release_facts"):
            et = "edition" if r[1] else "movie"
            eid = r[1] or r[0]
            add_assert(et, eid, r[2], r[3], r[4], "canonical", r[5], r[6] or r[1] or r[0], r[7], r[8], r[9], r[10])
        # movie_alias（任务4）
        for r in conn.execute(
                "SELECT movie_id, title_raw, title_canon, source, source_locator, run_id "
                "FROM movie_alias"):
            add_assert("movie", r[0], "title_alias", r[1], r[2], "alias", r[3], r[4],
                       "R3 alias 保留", r[5], None, "candidate")
        # person_alias（任务5）
        for r in conn.execute(
                "SELECT person_id, name_raw, name_canon, source, source_locator, run_id "
                "FROM person_alias"):
            add_assert("person", r[0], "name", r[1], r[2], "canonical", r[3], r[4],
                       "person_alias 保留原文", r[5], None, "candidate")
        # movie_person（角色证据）
        for r in conn.execute(
                "SELECT movie_id, person_id, role, nconst, source, source_locator, run_id "
                "FROM movie_person"):
            add_assert("movie", r[0], "person_credit", f"{r[1]}:{r[2]}", f"{r[1]}:{r[2]}",
                       f"work_{r[2]}", r[4], r[5] or r[3], "movie_person 角色", r[6], None, "selected")

        with conn.cursor() as cur:
            for row in ins:
                cur.execute(
                    "INSERT INTO lineage_field_assertion (entity_type,entity_id,field_name,value_role,"
                    "raw_value,normalized_value,source,source_record_id,field_locator,extraction_rule,"
                    "rule_version,confidence,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", row)

        # ---- 最终值 ----
        conn.execute("""
            INSERT INTO lineage_field_final (entity_type,entity_id,field_name,selected_assertion_uids,
                                             decision_reason,rules_version,run_id)
            SELECT entity_type, entity_id, field_name,
                   jsonb_agg(assertion_uid),
                   'R2+R3 自动选定（selected 断言集）', min(rule_version), %s
            FROM lineage_field_assertion
            WHERE entity_type='movie' AND status='selected'
              AND field_name IN ('title','release_year','genres','directors','starring')
            GROUP BY entity_type, entity_id, field_name
            ON CONFLICT (entity_type, entity_id, field_name) DO NOTHING
            """, (run_id,))
        conn.execute("""
            INSERT INTO lineage_field_final (entity_type,entity_id,field_name,selected_assertion_uids,
                                             decision_reason,rules_version,run_id)
            SELECT 'movie', movie_id, field_name, '[]'::jsonb,
                   'conflict 保留（候选集合见断言行，不编造）', min(rules_version), %s
            FROM field_assertion
            WHERE status='conflict'
            GROUP BY movie_id, field_name
            ON CONFLICT (entity_type, entity_id, field_name) DO NOTHING
            """, (run_id,))
        # release_year 最终值（任务6 verified/inferred）
        conn.execute("""
            INSERT INTO lineage_field_final (entity_type,entity_id,field_name,selected_assertion_uids,
                                             decision_reason,rules_version,run_id)
            SELECT 'movie', movie_id, fact_type, jsonb_agg(la.assertion_uid),
                   '任务6 补全规则选定', min(la.rule_version), %s
            FROM release_facts rf
            JOIN lineage_field_assertion la
              ON la.entity_id=rf.movie_id AND la.field_name=rf.fact_type
             AND la.normalized_value IS NOT DISTINCT FROM rf.value_norm
            WHERE rf.status IN ('verified','inferred')
            GROUP BY rf.movie_id, rf.fact_type
            ON CONFLICT (entity_type, entity_id, field_name) DO NOTHING
            """, (run_id,))

        # ---- 处理链 ----
        steps = []
        for a, b, dec, reason, rv, rn in conn.execute(
                "SELECT node_a, node_b, decision, reason, rules_version, run_id FROM movie_merge_decision"):
            op = "merge" if dec in ("auto_merge", "manual_merge") else (
                "manual_confirm" if reason.startswith("manual_confirm") else
                ("reject" if dec == "no_merge" else "select"))
            steps.append((None, op, "entity_pair", f"{a}|{b}", rv, rn, None, reason[:120]))
        for uid, eid, f, conf, ver in conn.execute(
                "SELECT assertion_uid, entity_id, field_name, confidence, rule_version "
                "FROM lineage_field_assertion WHERE status='selected'"):
            steps.append((None, "select", "field", f"{eid}|{f}|{uid}", ver, run_id, conf, "selected assertion"))
        for a, b, dec, reason, rn in conn.execute(
                "SELECT node_a, node_b, decision, reason, run_id FROM person_merge_decision"):
            op = "merge" if dec in ("auto_merge", "manual_merge") else (
                "manual_confirm" if reason.startswith("manual_confirm") else "reject")
            steps.append((None, op, "person_pair", f"{a}|{b}", "person_merge_v1.1", rn,
                          None, reason[:120]))
        with conn.cursor() as cur:
            for s in steps:
                cur.execute(
                    "INSERT INTO lineage_process_step (parent_step_id,op_type,target_kind,target_ref,"
                    "rule_id,run_id,confidence,note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", s)
        conn.commit()
        counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in
                  ("lineage_source_record", "lineage_field_assertion",
                   "lineage_field_final", "lineage_process_step")}
    print("==== 血缘加载完成 ====")
    print(counts, f"elapsed={round(time.time()-t0,1)}s")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="run_20261104-11")
    a = ap.parse_args()
    sys.exit(main(a.run_id))
