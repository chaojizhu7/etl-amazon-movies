# -*- coding: utf-8 -*-
"""QA-3 计数采集：抓取核心实体/关系表行数，输出 JSON 供幂等/恢复/规则变更前后对比。

用法：
  .venv\\Scripts\\python.exe src\\qa3_capture_counts.py --label run1_after
输出：
  logs/summary/qa3_counts_<label>.json
"""
import argparse
import json
import os
from datetime import datetime, timezone

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = dict(host="localhost", port=5432, dbname="etl_movie", user="postgres", password="postgres")

# 核心实体表 / 关系表 / 数据表（幂等口径：重跑不增不减）
CORE_TABLES = [
    "movie_entity", "product_movie", "movie_alias", "movie_manual_group",
    "movie_merge_candidate", "movie_merge_decision",
    "person_entity", "person_alias", "movie_person",
    "person_merge_candidate", "person_merge_decision",
    "review_mapping", "review_record_index", "review_anomaly",
    "product_classification", "release_facts", "field_assertion",
    "lineage_source_record", "lineage_field_assertion",
    "lineage_field_final", "lineage_process_step",
]
# 审计表（按设计追加，只作参考不参与幂等相等判定）
AUDIT_TABLES = ["etl_run_task", "quality_issue"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    a = ap.parse_args()
    out = {"label": a.label, "captured_at": datetime.now(timezone.utc).astimezone().isoformat(),
           "core": {}, "audit_total": {}, "review_mapping_by_run": {}}
    with psycopg.connect(**DB) as conn:
        for t in CORE_TABLES + AUDIT_TABLES:
            n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            (out["core"] if t in CORE_TABLES else out["audit_total"])[t] = n
        for run_id, n in conn.execute(
                "SELECT run_id, count(*) FROM review_mapping GROUP BY run_id ORDER BY 1").fetchall():
            out["review_mapping_by_run"][run_id] = n
    path = os.path.join(ROOT, "logs", "summary", f"qa3_counts_{a.label}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out["core"], ensure_ascii=False))
    print("saved:", path)


if __name__ == "__main__":
    main()
