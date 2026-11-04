# -*- coding: utf-8 -*-
"""Movie ETL DAG（Airflow 2.10.4）。

任务链：extract → crawl → classify → merge → enrich → dedup → lineage → quality_check
- 参数化：params.run_id / params.rule_versions（默认值见 RULE_VERSIONS）
- 幂等：task 函数先查 etl_run_task，success 直接跳过（重跑不产生重复实体/关系）
- 失败处置：retries=2 + on_failure 回调写 quality_issue（issue_type=task_failed）并告警
- 中断恢复：重跑同一 run_id 时只补跑 failed/未跑 task

WSL 运行命令：
  airflow dags trigger movie_etl_dag --conf '{"run_id": "run_etl_20261104-01"}'
  airflow tasks test movie_etl_dag classify 2026-11-04 --conf '{"run_id":"run_etl_20261104-01"}'
本地同构运行（无 Airflow 时）：
  .venv\\Scripts\\python.exe src/run_etl_local.py --run-id run_etl_20261104-01 [--resume]
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.db import provide_session

sys.path.insert(0, "/mnt/c/Users/28746/Desktop/etl-amazon/src")
import run_etl_local as runner  # noqa: E402  复用同一任务实现（保证与本地演示同构）

DEFAULT_RULES = runner.RULE_VERSIONS


def _task(task_id):
    def fn(**ctx):
        run_id = ctx["params"].get("run_id") or ctx["dag_run"].conf.get("run_id")
        runner.exec_task(run_id, task_id)
    return fn


@provide_session
def _on_failure(context, session=None):
    run_id = context["params"].get("run_id", "unknown")
    task_id = context["task_instance"].task_id
    err = str(context.get("exception"))[:500]
    from psycopg import connect
    with connect(host="localhost", port=5432, dbname="etl_movie",
                 user="postgres", password="postgres") as conn:
        conn.execute(
            "INSERT INTO quality_issue (run_id,task_id,issue_type,detail,severity) "
            "VALUES (%s,%s,'task_failed',%s,'error')", (run_id, task_id, err))
        conn.commit()
    print(f"[ALERT][quality_issue] run={run_id} task={task_id} failed: {err}")


with DAG(
    dag_id="movie_etl_dag",
    start_date=datetime(2026, 11, 1),
    schedule=None,                       # 手动触发（作业要求：可控可复现）
    catchup=False,
    params={"run_id": "run_etl_20261104-01", "rule_versions": DEFAULT_RULES},
    default_args={"retries": 2, "retry_delay": 60, "on_failure": _on_failure},
    tags=["etl", "movie", "task1-9"],
) as dag:
    t_extract = PythonOperator(task_id="extract", python_callable=_task("extract"))
    t_crawl = PythonOperator(task_id="crawl", python_callable=_task("crawl"))
    t_classify = PythonOperator(task_id="classify", python_callable=_task("classify"))
    t_merge = PythonOperator(task_id="merge", python_callable=_task("merge"))
    t_enrich = PythonOperator(task_id="enrich", python_callable=_task("enrich"))
    t_dedup = PythonOperator(task_id="dedup", python_callable=_task("dedup"))
    t_lineage = PythonOperator(task_id="lineage", python_callable=_task("lineage"))
    t_quality = PythonOperator(task_id="quality_check", python_callable=_task("quality_check"))

    (t_extract >> t_crawl >> t_classify >> t_merge >> t_enrich
     >> t_dedup >> t_lineage >> t_quality)
