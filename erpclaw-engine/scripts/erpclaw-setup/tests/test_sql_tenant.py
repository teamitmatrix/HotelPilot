"""Tenant SQL rewriter: WHERE injection, param index, INSERT column bind."""
import os
import sqlite3
import sys

SETUP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SETUP_DIR, "lib"))

from erpclaw_lib.sql_tenant import apply_tenant_predicate, sqlite_tenant_column
from erpclaw_lib.db import get_connection


def _col(table: str) -> str | None:
    return {
        "customer": "company_id",
        "hospitalityclaw_reservation": "tenant_id",
        "company": "id",
        "webclaw_user": "tenant_id",
    }.get(table)


def test_select_injects_before_limit():
    sql = "SELECT id, name FROM customer WHERE status = ? ORDER BY name LIMIT ? OFFSET ?"
    new_sql, params = apply_tenant_predicate(sql, ["active", 20, 0], "hotel-1", _col)
    assert "customer.company_id = ?" in new_sql
    assert params == ["active", "hotel-1", 20, 0]
    assert new_sql.upper().index("COMPANY_ID") < new_sql.upper().index("ORDER BY")


def test_select_adds_where_when_missing():
    sql = "SELECT * FROM customer AS c ORDER BY c.name LIMIT ?"
    new_sql, params = apply_tenant_predicate(sql, [20], "hotel-1", _col)
    assert "WHERE c.company_id = ?" in new_sql
    assert params == ["hotel-1", 20]


def test_named_params_use_current_tenant_id():
    sql = "SELECT * FROM customer WHERE status = :status"
    new_sql, params = apply_tenant_predicate(
        sql, {"status": "active"}, "hotel-1", _col
    )
    assert "customer.company_id = :current_tenant_id" in new_sql
    assert params["current_tenant_id"] == "hotel-1"
    assert params["status"] == "active"


def test_update_and_delete_are_scoped():
    sql, params = apply_tenant_predicate(
        "UPDATE customer SET status = ? WHERE id = ?",
        ["open", "c1"],
        "hotel-1",
        _col,
    )
    assert "AND customer.company_id = ?" in sql
    assert params == ["open", "c1", "hotel-1"]

    sql, params = apply_tenant_predicate(
        "DELETE FROM hospitalityclaw_reservation WHERE id = ?",
        ["r1"],
        "hotel-1",
        _col,
    )
    assert "tenant_id = ?" in sql
    assert params[-1] == "hotel-1"


def test_insert_appends_tenant_column():
    sql, params = apply_tenant_predicate(
        "INSERT INTO customer (id, name) VALUES (?, ?)",
        ["c1", "Ada"],
        "hotel-1",
        _col,
    )
    assert "company_id" in sql
    assert sql.count("?") == 3
    assert params == ["c1", "Ada", "hotel-1"]


def test_insert_skips_when_column_present():
    sql = "INSERT INTO customer (id, name, company_id) VALUES (?, ?, ?)"
    new_sql, params = apply_tenant_predicate(sql, ["c1", "Ada", "hotel-1"], "hotel-1", _col)
    assert new_sql == sql
    assert params == ["c1", "Ada", "hotel-1"]


def test_ddl_and_pragma_are_untouched():
    for sql in (
        "PRAGMA foreign_keys=ON",
        "CREATE TABLE customer (id TEXT)",
        "BEGIN",
    ):
        new_sql, params = apply_tenant_predicate(sql, None, "hotel-1", _col)
        assert new_sql == sql
        assert params is None


def test_unscoped_table_untouched():
    sql = "SELECT * FROM currency ORDER BY code"
    new_sql, params = apply_tenant_predicate(sql, None, "hotel-1", _col)
    assert new_sql == sql


def test_connection_wrapper_enforces_company_id(tmp_path, monkeypatch):
    monkeypatch.setenv("ERPCLAW_TENANT_ID", "hotel-a")
    monkeypatch.delenv("ERPCLAW_DB_DIALECT", raising=False)
    path = str(tmp_path / "tenant.sqlite")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE customer (id TEXT, name TEXT, company_id TEXT);"
        "INSERT INTO customer VALUES ('1','A','hotel-a');"
        "INSERT INTO customer VALUES ('2','B','hotel-b');"
    )
    conn.commit()
    conn.close()

    wrapped = get_connection(path)
    try:
        rows = wrapped.execute("SELECT name FROM customer ORDER BY name").fetchall()
        assert [r["name"] for r in rows] == ["A"]
    finally:
        wrapped.close()


def test_sqlite_tenant_column_prefers_tenant_id():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE hospitalityclaw_guest (id TEXT, tenant_id TEXT)")
    conn.execute("CREATE TABLE customer (id TEXT, company_id TEXT)")
    conn.execute("CREATE TABLE company (id TEXT, name TEXT)")
    assert sqlite_tenant_column(conn, "hospitalityclaw_guest") == "tenant_id"
    assert sqlite_tenant_column(conn, "customer") == "company_id"
    assert sqlite_tenant_column(conn, "company") == "id"
    assert sqlite_tenant_column(conn, "sqlite_master") is None
