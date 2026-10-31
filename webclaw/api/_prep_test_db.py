"""Create pms_test and run init_postgres against it."""
import os
import runpy
import sys
from pathlib import Path

API = Path(__file__).resolve().parent
ROOT = API.parents[1]
sys.path.insert(0, str(API))

import env_loader

env_loader.load_api_env()

TEST_DB = (os.environ.get("WEBCLAW_TEST_DB") or "pms_test").strip()
host = (os.environ.get("DB_HOST") or "localhost").strip()
port = int((os.environ.get("DB_PORT") or "5432").strip() or "5432")
user = (os.environ.get("DB_USER") or "postgres").strip()
password = os.environ.get("DB_PASSWORD") or ""

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

kw = dict(host=host, port=port, user=user, password=password)
try:
    conn = psycopg2.connect(dbname="postgres", **kw)
except Exception:
    conn = psycopg2.connect(dbname=os.environ.get("DB_NAME") or "pms", **kw)
conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
cur = conn.cursor()
cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
if cur.fetchone() is None:
    cur.execute(f'CREATE DATABASE "{TEST_DB}"')
    print("created", TEST_DB)
else:
    print("exists", TEST_DB)
cur.close()
conn.close()

os.environ["DB_TYPE"] = "postgres"
os.environ["DB_NAME"] = TEST_DB
for k in ("WEBCLAW_DB_URL", "ERPCLAW_DB_URL"):
    os.environ.pop(k, None)
env_loader.publish_db_env()

init = ROOT / "erpclaw-engine" / "scripts" / "init_postgres.py"
sys.argv = [str(init)]
print("init_postgres on", TEST_DB)
try:
    runpy.run_path(str(init), run_name="__main__")
except SystemExit as exc:
    if exc.code not in (0, None):
        raise SystemExit(exc.code)
print("done")
