#!/bin/bash
# Install Airflow 2.10.4 (Python 3.12) into WSL user site. Idempotent.
set -e
export PATH="$HOME/.local/bin:$PATH"

if python3 -c "import airflow" 2>/dev/null; then
  echo "airflow already installed: $(python3 -c 'import airflow; print(airflow.__version__)')"
  exit 0
fi

echo "== fetch constraints =="
curl -sS -o /tmp/constraints.txt \
  https://raw.githubusercontent.com/apache/airflow/constraints-2.10.4/constraints-3.12.txt
wc -l /tmp/constraints.txt

echo "== pip install apache-airflow =="
python3 -m pip install --user --break-system-packages \
  "apache-airflow==2.10.4" \
  --constraint /tmp/constraints.txt \
  -i https://pypi.tuna.tsinghua.edu.cn/simple

echo "== done =="
python3 -c "import airflow; print('airflow', airflow.__version__)"
