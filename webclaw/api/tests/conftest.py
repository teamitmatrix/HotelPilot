"""Pytest fixtures — Postgres only (no temp .sqlite).

Requires the local PostgreSQL 18 instance (``DB_*`` in ``api/.env``).
Default database: ``pms_test``.

    DB_NAME=pms_test python erpclaw-engine/scripts/init_postgres.py
    cd webclaw/api && pytest

Override the test database name with ``WEBCLAW_TEST_DB``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

# Load api/.env before anything else so DB_* is available.
import env_loader  # noqa: E402

env_loader.load_api_env()

TEST_DB = (os.environ.get("WEBCLAW_TEST_DB") or "pms_test").strip() or "pms_test"


def _admin_url(dbname: str | None = None) -> str:
    from env_loader import postgres_url_from_env

    url = postgres_url_from_env()
    if not url:
        raise RuntimeError(
            "Postgres required for tests. Set DB_HOST/DB_PORT/DB_NAME/DB_USER/"
            "DB_PASSWORD for the local PostgreSQL 18 instance."
        )
    if dbname is None:
        return url
    # Replace path database name.
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=f"/{dbname}"))


def _ensure_test_database() -> None:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    # Connect to maintenance DB to create pms_test if missing.
    admin = _admin_url(os.environ.get("DB_NAME") or "pms")
    # Prefer connecting to 'postgres' catalog for CREATE DATABASE.
    try:
        conn = psycopg2.connect(_admin_url("postgres"))
    except Exception:
        conn = psycopg2.connect(admin)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
    if cur.fetchone() is None:
        cur.execute(f'CREATE DATABASE "{TEST_DB}"')
    cur.close()
    conn.close()


def _clear_stale_test_sessions() -> None:
    """Terminate connections left behind by an interrupted local test run."""
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    conn = psycopg2.connect(_admin_url("postgres"))
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = %s AND pid <> pg_backend_pid()
            """,
            (TEST_DB,),
        )
        cur.close()
    finally:
        conn.close()


def _force_test_env() -> None:
    os.environ["DB_TYPE"] = "postgres"
    os.environ["DB_NAME"] = TEST_DB
    # Turn leaked transactions or lock contention into prompt failures rather
    # than an indefinitely hung test run.
    os.environ["PGOPTIONS"] = (
        "-c idle_in_transaction_session_timeout=10000 "
        "-c lock_timeout=15000"
    )
    os.environ.pop("WEBCLAW_ALLOW_SQLITE", None)
    # Clear URL overrides so discrete DB_* win for the test DB name.
    for key in ("WEBCLAW_DB_URL", "ERPCLAW_DB_URL"):
        os.environ.pop(key, None)
    env_loader.publish_db_env()
    # Point legacy path vars at non-sqlite sentinels so accidental file opens fail loudly.
    os.environ["WEBCLAW_DB_PATH"] = str(API_DIR / ".pytest_no_sqlite_webclaw")
    os.environ["ERPCLAW_DB_PATH"] = str(API_DIR / ".pytest_no_sqlite_erp")


def _init_schema_if_needed() -> None:
    """Run init_postgres against pms_test once when webclaw schema is missing."""
    import psycopg2

    url = _admin_url(TEST_DB)
    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1 FROM information_schema.schemata WHERE schema_name = 'webclaw'
        """
    )
    has_webclaw = cur.fetchone() is not None
    cur.close()
    conn.close()
    if has_webclaw:
        return

    root = API_DIR.parents[1]
    init = root / "erpclaw-engine" / "scripts" / "init_postgres.py"
    if not init.is_file():
        raise RuntimeError(f"init_postgres.py not found at {init}")
    import runpy

    old = dict(os.environ)
    try:
        os.environ["DB_TYPE"] = "postgres"
        os.environ["DB_NAME"] = TEST_DB
        os.environ.pop("WEBCLAW_DB_URL", None)
        os.environ.pop("ERPCLAW_DB_URL", None)
        sys.argv = [str(init)]
        runpy.run_path(str(init), run_name="__main__")
    finally:
        os.environ.clear()
        os.environ.update(old)
        _force_test_env()


def _close_quietly(conn) -> None:
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass


def _truncate_schema(conn, schema: str) -> None:
    rows = conn.execute(
        """
        SELECT tablename FROM pg_tables
        WHERE schemaname = ?
        """,
        (schema,),
    ).fetchall()
    tables = [r[0] if not hasattr(r, "keys") else r["tablename"] for r in rows]
    if not tables:
        return
    # Quote identifiers
    qualified = ", ".join(f'"{schema}"."{t}"' for t in tables)
    conn.execute(f"TRUNCATE {qualified} RESTART IDENTITY CASCADE")
    conn.commit()


@pytest.fixture(scope="session")
def pg_session():
    """Ensure ``pms_test`` exists and has schema; configure process env."""
    try:
        _ensure_test_database()
        _clear_stale_test_sessions()
        _force_test_env()
        _init_schema_if_needed()
        _force_test_env()
    except Exception as exc:
        pytest.exit(
            f"Postgres test DB unavailable ({exc}). "
            "Set DB_* in webclaw/api/.env for the local PostgreSQL 18 instance, "
            "then: DB_NAME=pms_test python erpclaw-engine/scripts/init_postgres.py",
            returncode=1,
        )
    return {"db_name": TEST_DB}


@pytest.fixture(autouse=True)
def _postgres_only(monkeypatch, pg_session):
    """Every test runs with DB_TYPE=postgres against pms_test — never SQLite."""
    monkeypatch.setenv("DB_TYPE", "postgres")
    monkeypatch.setenv("DB_NAME", TEST_DB)
    monkeypatch.setenv("WEBCLAW_ENV", "development")
    monkeypatch.delenv("WEBCLAW_ALLOW_SQLITE", raising=False)
    monkeypatch.delenv("WEBCLAW_DB_URL", raising=False)
    monkeypatch.delenv("ERPCLAW_DB_URL", raising=False)
    env_loader.publish_db_env()
    # Reset schema-init flags so ensure_* helpers stay honest across tests.
    import db as dbmod

    dbmod._tables_initialized = False
    try:
        import pg_rls

        pg_rls._installed = False
    except Exception:
        pass


@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch, pg_session):
    """Fresh empty ``webclaw`` schema for every test (no temp ``.sqlite``).

    Returns a dict (not a file path). ERP/hotel tables are truncated only when
    a test also requests :func:`setup_dbs`.
    """
    import db as dbmod

    dbmod._tables_initialized = False
    conn = dbmod.get_connection()
    _truncate_schema(conn, "webclaw")
    from init_webclaw_db import init_tables

    init_tables(conn)
    try:
        conn.commit()
    finally:
        conn.close()

    opened: list = []
    real_gc = dbmod.get_connection
    real_gs = dbmod.get_skill_db

    def _track(conn):
        if conn is not None:
            opened.append(conn)
        return conn

    monkeypatch.setattr(dbmod, "get_connection", lambda path=None: _track(real_gc(path)))
    monkeypatch.setattr(
        dbmod, "get_skill_db", lambda skill_name=None: _track(real_gs(skill_name or "erpclaw"))
    )
    yield {"dialect": "postgresql", "db": TEST_DB}
    for leftover in opened:
        _close_quietly(leftover)


@pytest.fixture
def setup_dbs(monkeypatch, pg_session):
    """Fresh ``webclaw`` + truncated ``public`` ERP/hotel tables.

    Monkeypatches ``db.get_connection`` and ``db.get_skill_db``.
    Returns ``{"web": "postgresql", "erp": "postgresql"}`` (no file paths).
    """
    import db as dbmod

    dbmod._tables_initialized = False
    web = dbmod.get_connection()
    _truncate_schema(web, "webclaw")
    from init_webclaw_db import init_tables

    init_tables(web)
    web.commit()
    web.close()

    erp = dbmod.get_skill_db("erpclaw")
    # Truncate public tenant-ish tables used by API tests (keep schema).
    _truncate_schema(erp, "public")
    erp.close()
    # Re-apply minimal ERP/hospitality DDL is heavy; tables remain from session init.
    # Ensure hosp/ERP core exist after truncate (they do — truncate keeps structure).

    # Capture unbound originals BEFORE setattr — mocking to db.get_connection
    # after patch would recurse infinitely.
    real_gc = dbmod.get_connection
    real_gs = dbmod.get_skill_db

    def mock_get_connection(path=None):
        return real_gc()

    def mock_get_skill_db(skill_name=None):
        return real_gs(skill_name or "erpclaw")

    monkeypatch.setattr("db.get_connection", mock_get_connection)
    monkeypatch.setattr("db.get_skill_db", mock_get_skill_db)
    return {"web": "postgresql", "erp": "postgresql", "db": TEST_DB}


@pytest.fixture
def setup_audit(setup_dbs):
    """Alias used by night-audit tests (was a temp .sqlite pair)."""
    return setup_dbs


@pytest.fixture
def setup_guest(setup_dbs, tmp_path, monkeypatch):
    """ERP + platform DBs plus a docs dir for guest portal tests."""
    docs = tmp_path / "guest-docs"
    docs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WEBCLAW_GUEST_DOCS_DIR", str(docs))
    return {**setup_dbs, "docs": str(docs)}


@pytest.fixture
def client(setup_test_db):
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app)


@pytest.fixture
def client_with_erp(setup_dbs):
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app)
