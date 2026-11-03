#!/bin/bash
# Init Airflow metadata DB + point dags_folder to project. Idempotent.
set -e
export PATH="$HOME/.local/bin:$PATH"
export AIRFLOW_HOME="$HOME/airflow_etl"
export PYTHONPATH="/mnt/c/Users/28746/Desktop/etl-amazon/etl/dags"

if [ ! -f "$AIRFLOW_HOME/airflow.db" ]; then
  echo "== airflow db init =="
  airflow db init 2>&1 | tail -3
else
  echo "== airflow.db already exists, skip init =="
fi

# ensure dags_folder points to project (idempotent overwrite of setting)
if ! grep -q "^dags_folder = /mnt/c/Users/28746/Desktop/etl-amazon/etl/dags" "$AIRFLOW_HOME/airflow.cfg" 2>/dev/null; then
  sed -i "s|^dags_folder = .*|dags_folder = /mnt/c/Users/28746/Desktop/etl-amazon/etl/dags|" "$AIRFLOW_HOME/airflow.cfg"
  echo "dags_folder updated"
fi

echo "== airflow version =="
airflow version
echo "== dags list =="
airflow dags list 2>&1 | tail -5
