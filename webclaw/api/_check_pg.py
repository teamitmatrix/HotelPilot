import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import env_loader

env_loader.load_api_env()
import psycopg2

u = env_loader.postgres_url_from_env()
print("connected_url_host", os.environ.get("DB_HOST"), "db", os.environ.get("DB_NAME"))
c = psycopg2.connect(u)
cur = c.cursor()
cur.execute("SELECT current_database()")
print("current_db", cur.fetchone()[0])
cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", ("pms_test",))
print("pms_test_exists", cur.fetchone() is not None)
