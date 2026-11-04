# -*- coding: utf-8 -*-
"""ETL quality_check 步骤：跨表一致性校验，违例写 quality_issue。

Usage: .venv\\Scripts\\python.exe src\\quality_check.py --run-id <run_id>
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import psycopg

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB = dict(host="localhost", port="5432", dbname="etl_movie", user="postgres", password="postgres")
EXPECT_QUEUE = 253_059
EXPECT_REVIEWS = 7_911_684


def main(run_id):
    checks = []

    def chk(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return ok

    with psycopg.connect(**DB) as conn:
        with open(os.path.join(ROOT, "sql", "10_etl_infra.sql"), encoding="utf-8") as f:
            conn.execute(f.read())
        q = conn.execute("SELECT count(*) FROM product_crawl_queue").fetchone()[0]
        chk("queue_total", q == EXPECT_QUEUE, f"{q} == {EXPECT_QUEUE}")
        st = conn.execute("SELECT count(*) FROM product_classification").fetchone()[0]
        chk("classification_total", st == EXPECT_QUEUE, f"{st} == {EXPECT_QUEUE}")
        rm = conn.execute("SELECT count(*) FROM review_mapping").fetchone()[0]
        chk("review_mapping_total", rm == EXPECT_REVIEWS, f"{rm} == {EXPECT_REVIEWS}")
        orph = conn.execute(
            "SELECT count(*) FROM product_movie pm LEFT JOIN movie_entity me "
            "ON me.movie_id=pm.movie_id WHERE me.movie_id IS NULL").fetchone()[0]
        chk("fk_orphan_product_movie", orph == 0, f"orphans={orph}")
        orph2 = conn.execute(
            "SELECT count(*) FROM release_facts rf LEFT JOIN movie_entity me "
            "ON me.movie_id=rf.movie_id WHERE me.movie_id IS NULL").fetchone()[0]
        chk("fk_orphan_release_facts", orph2 == 0, f"orphans={orph2}")
        orph3 = conn.execute(
            "SELECT count(*) FROM field_assertion fa LEFT JOIN movie_entity me "
            "ON me.movie_id=fa.movie_id WHERE me.movie_id IS NULL").fetchone()[0]
        chk("fk_orphan_field_assertion", orph3 == 0, f"orphans={orph3}")
        orph4 = conn.execute(
            "SELECT count(*) FROM movie_person mp LEFT JOIN person_entity pe "
            "ON pe.person_id=mp.person_id WHERE pe.person_id IS NULL").fetchone()[0]
        chk("fk_orphan_movie_person", orph4 == 0, f"orphans={orph4}")
        mp_dup = conn.execute(
            "SELECT count(*) FROM (SELECT asin, movie_id, count(*) c FROM product_movie "
            "GROUP BY 1,2 HAVING count(*)>1) t").fetchone()[0]
        chk("no_duplicate_product_movie", mp_dup == 0, f"dup_keys={mp_dup}")
        # 互斥状态数之和（铁律7）
        cls_sum = conn.execute("SELECT count(*) FROM product_classification").fetchone()[0]
        chk("classification_mutex_sum", cls_sum == EXPECT_QUEUE,
            f"分类状态互斥合计 {cls_sum}")
        # 人工合并保持性（验收红线：人工裁定的合并不得被重算回滚）
        named = {
            "mv_000006": ["6302914485", "B00005NBBJ", "B00005TPCG", "B005FOW9SW", "B008UY8FI2"],
            "mv_000212": ["B00004CYNY", "B000TGJ7ZY"],
            "mv_000204": ["B002ZG98VE", "B006WXHJGC"],
            "mv_000084": ["B0006Z2KIY", "B0036JQ7S6"],
        }
        groups = {}
        for cid, a in conn.execute("SELECT canonical_movie_id, asin FROM movie_manual_group"):
            groups.setdefault(cid, []).append(a)
        bad = []
        for cid, asins in sorted(groups.items()):
            if len(asins) < 2:
                bad.append(f"{cid}: 成员<2")
                continue
            mids = {r[0] for r in conn.execute(
                "SELECT movie_id FROM product_movie WHERE asin = ANY(%s) AND relation_type='single'",
                (asins,))}
            if len(mids) != 1:
                bad.append(f"{cid}: 成员分裂为 {sorted(mids)}")
            elif next(iter(mids)) != cid:
                bad.append(f"{cid}: 簇 id={next(iter(mids))}≠canonical")
        for cid, asins in named.items():
            got = set(groups.get(cid, []))
            if not set(asins) <= got:
                bad.append(f"named {cid}: 缺成员 {sorted(set(asins) - got)}")
        chk("manual_merge_preserved",
            len(groups) >= 32 and not bad,
            f"人工组 {len(groups)} 个（应≥32）, 违例 {bad[:4] if bad else '无'}")
        conn.commit()
        failed = [c for c in checks if not c["ok"]]
        for c in failed:
            conn.execute(
                "INSERT INTO quality_issue (run_id,task_id,issue_type,detail,severity) "
                "VALUES (%s,'quality_check','check_violation',%s,'error')",
                (run_id, f"{c['check']}: {c['detail']}"))
        conn.commit()
    out = {"run_id": run_id,
           "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "checks": checks, "violations": len(failed)}
    p = os.path.join(ROOT, "logs", "summary", "quality_check_report.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()
    sys.exit(main(a.run_id))
