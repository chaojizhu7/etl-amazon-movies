# -*- coding: utf-8 -*-
"""Environment self-check: Python, libraries, database, disk space.

Outputs a checklist table. Idempotent, read-only (except no side effects).
Expected usage (from project root, with venv active or via .venv path):
    .venv\\Scripts\\python.exe scripts\\env_check.py
"""
import importlib
import os
import shutil
import sys
import time

LIBS = ["scrapy", "pandas", "psycopg", "tqdm", "charset_normalizer"]
DB_HOST = os.environ.get("ETL_DB_HOST", "localhost")
DB_PORT = os.environ.get("ETL_DB_PORT", "5432")
DB_NAME = os.environ.get("ETL_DB_NAME", "etl_movie")
DB_USER = os.environ.get("ETL_DB_USER", "postgres")
DB_PASS = os.environ.get("ETL_DB_PASSWORD", "postgres")


def fmt_bytes(n: int) -> str:
    return f"{n:,} B ({n / 1024**3:.2f} GiB)"


def check(label: str, ok: bool, detail: str) -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"| {label:<22} | {mark:<4} | {detail} |")


def main() -> int:
    print(f"Env check at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Project root: {os.getcwd()}")
    print()
    print("| 检查项                 | 结果 | 详情 |")
    print("|------------------------|------|------|")

    # Python
    v = sys.version.split()[0]
    check("Python", sys.version_info >= (3, 10), f"{v} @ {sys.executable}")

    # Libraries
    for lib in LIBS:
        try:
            m = importlib.import_module(lib)
            ver = getattr(m, "__version__", getattr(m, "version", "?"))
            check(lib, True, str(ver))
        except Exception as e:
            check(lib, False, f"import failed: {e}")

    # Database
    try:
        import psycopg

        with psycopg.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS,
            connect_timeout=5,
        ) as conn:
            cur = conn.execute("SELECT current_database(), version()")
            db, ver = cur.fetchone()
            check("PostgreSQL", True, f"db={db}, {ver.split(' on ')[0]}")
    except Exception as e:
        check("PostgreSQL", False, f"connect failed: {e}")

    # Disk
    usage = shutil.disk_usage(os.getcwd())
    ok = usage.free > 20 * 1024**3
    check(
        "Disk free",
        ok,
        f"{fmt_bytes(usage.free)} free of {fmt_bytes(usage.total)} (need >= 20 GiB)",
    )

    # Data file presence (informational)
    gz = os.path.join("data", "raw", "movies.txt.gz")
    if os.path.isfile(gz):
        check("movies.txt.gz", True, f"present, {fmt_bytes(os.path.getsize(gz))}")
    else:
        check("movies.txt.gz", False, "not present (download pending)")

    print()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
