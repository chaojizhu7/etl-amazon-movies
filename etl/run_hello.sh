#!/bin/bash
export PATH="$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export AIRFLOW_HOME="$HOME/airflow_etl"
set -o pipefail
echo "== airflow dags test hello_etl 2026-11-03 =="
airflow dags test hello_etl 2026-11-03 2>&1 | tail -30
echo "== marker file =="
cat /mnt/c/Users/28746/Desktop/etl-amazon/logs/summary/hello_etl_2026-11-03.txt
echo "== task log file =="
find "$AIRFLOW_HOME/logs" -name "*.log" -newer /tmp/constraints.txt 2>/dev/null | head -5
LOGF=$(find "$AIRFLOW_HOME/logs/dag_id=hello_etl" -name "*.log" 2>/dev/null | head -1)
if [ -n "$LOGF" ]; then
  echo "--- tail of $LOGF ---"
  tail -15 "$LOGF"
fi
