# -*- coding: utf-8 -*-
"""Hello-world ETL DAG: proves job definition, dependencies, params, logging.

Two tasks with dependency + conf parameter, writes a marker file.
Run: airflow dags test hello_etl 2026-11-03
"""
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

# Project logs dir (WSL view of Windows project)
LOG_DIR = Path("/mnt/c/Users/28746/Desktop/etl-amazon/logs/summary")
PARAM = {"greeting": "hello etl", "parser_version": "v0.1"}


def extract(**ctx):
    msg = f"[{ctx['run_id']}] extract: {PARAM['greeting']} (parser={PARAM['parser_version']})"
    print(msg)
    return "extracted"


def transform(**ctx):
    ti = ctx["ti"]
    upstream = ti.xcom_pull(task_ids="extract")
    msg = f"[{ctx['run_id']}] transform: got '{upstream}', converting to upper"
    print(msg)
    return upstream.upper()


def load(**ctx):
    ti = ctx["ti"]
    value = ti.xcom_pull(task_ids="transform")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = LOG_DIR / f"hello_etl_{ctx['ds']}.txt"
    out.write_text(f"run_id={ctx['run_id']}\nvalue={value}\nparser={PARAM['parser_version']}\n", encoding="utf-8")
    print(f"[{ctx['run_id']}] load: wrote {out}")
    return str(out)


with DAG(
    dag_id="hello_etl",
    description="Minimal ETL proof: extract -> transform -> load",
    start_date=datetime(2026, 11, 1),
    schedule=None,
    catchup=False,
    default_args={"retries": 1, "retry_delay": timedelta(seconds=10)},
    tags=["etl", "hello"],
) as dag:
    t1 = PythonOperator(task_id="extract", python_callable=extract)
    t2 = PythonOperator(task_id="transform", python_callable=transform)
    t3 = PythonOperator(task_id="load", python_callable=load)
    t1 >> t2 >> t3
