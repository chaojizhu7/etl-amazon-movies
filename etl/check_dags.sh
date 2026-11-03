#!/bin/bash
export PATH="$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export AIRFLOW_HOME="$HOME/airflow_etl"
echo "== import errors =="
airflow dags list-import-errors 2>&1 | sed -n '1,15p'
echo "== dags dir =="
ls -la /mnt/c/Users/28746/Desktop/etl-amazon/etl/dags/
echo "== dags list (grep hello) =="
airflow dags list 2>&1 | grep -i hello || echo "hello_etl NOT listed"
echo "== cfg dags_folder =="
grep '^dags_folder' "$AIRFLOW_HOME/airflow.cfg"
