#!/usr/bin/env python3
"""Verify SQLite -> PostgreSQL migration for the PMS databases.

Compares:

* ``data/webclaw.sqlite``  <->  PostgreSQL schema ``webclaw``
* ``data/data.sqlite``     <->  PostgreSQL schema ``public``

Checks performed:

1. Per-table row counts (zero record loss)
2. Primary-key / foreign-key constraints present + FK orphan scan
3. Test INSERT (UUID TEXT PKs; SERIAL sequences when present)
4. Sample application queries (shape + timing) on both backends

Usage (from ``pms/`` workspace root):

    python erpclaw-engine/scripts/verify_sqlite_to_postgres.py
    python erpclaw-engine/scripts/verify_sqlite_to_postgres.py --only-webclaw
    python erpclaw-engine/scripts/verify_sqlite_to_postgres.py --json-report verify.json

Exit code 0 = all checks passed; 1 = one or more failures.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "erpclaw-engine" / "scripts" / "erpclaw-setup" / "lib"
SETUP = ROOT / "erpclaw-engine" / "scripts" / "erpclaw-setup"

_SKIP_TABLES = frozenset({"sqlite_sequence", "sqlite_stat1", "sqlite_stat4"})


# ── small env / connection helpers (mirrors migrate script) ─────────────────


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = os.path.expanduser(value)


def _resolve_workspace_path(raw: str | None, *default_parts: str) -> Path:
    if not raw:
        return ROOT.joinpath(*default_parts)
    path = Path(os.path.expanduser(raw))
    if not path.is_absolute():
        path = ROOT / path
    return path


def _postgres_url_from_env() -> str:
    explicit = (
        os.environ.get("ERPCLAW_DB_URL")
        or os.environ.get("WEBCLAW_DB_URL")
        or ""
    ).strip()
    if explicit.lower().startswith(("postgres://", "postgresql://")):
        return explicit
    host = (os.environ.get("DB_HOST") or "localhost").strip() or "localhost"
    port = (os.environ.get("DB_PORT") or "5432").strip() or "5432"
    name = (os.environ.get("DB_NAME") or "pms").strip() or "pms"
    user = (os.environ.get("DB_USER") or "postgres").strip() or "postgres"
    password = os.environ.get("DB_PASSWORD") or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    auth = quote(user, safe="")
    if password:
        auth = f"{auth}:{quote(password, safe='')}"
    return f"postgresql://{auth}@{host}:{port}/{quote(name, safe='')}"


def _redact_url(url: str) -> str:
    return re.sub(r"(://[^:/]+):([^@/]+)@", r"\1:***@", url)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


# ── result model ────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    started_at: str
    postgres: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, name: str, passed: bool, detail: str = "", **data: Any) -> CheckResult:
        item = CheckResult(name=name, passed=passed, detail=detail, data=data)
        self.checks.append(item)
        mark = "PASS" if passed else "FAIL"
        _log(f"  [{mark}] {name}: {detail}" if detail else f"  [{mark}] {name}")
        return item


# ── connections ─────────────────────────────────────────────────────────────


def _pg_connect(url: str):
    import psycopg2
    from psycopg2.extras import DictCursor

    conn = psycopg2.connect(url, cursor_factory=DictCursor)
    conn.autocommit = False
    return conn


def _sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [r[0] for r in rows if r[0] not in _SKIP_TABLES]


def _sqlite_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _pg_tables(pg, schema: str) -> set[str]:
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            """,
            (schema,),
        )
        return {r[0] for r in cur.fetchall()}


def _pg_count(pg, schema: str, table: str) -> int:
    with pg.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {_qi(schema)}.{_qi(table)}")
        return int(cur.fetchone()[0])


# ── 1. counts ───────────────────────────────────────────────────────────────


def check_counts(
    report: Report,
    sqlite_path: Path,
    pg,
    schema: str,
    label: str,
) -> None:
    _log(f"\n== 1. Row counts: {label} ==")
    if not sqlite_path.is_file():
        report.add(f"{label}: sqlite file", False, f"missing {sqlite_path}")
        return

    src = sqlite3.connect(str(sqlite_path))
    try:
        tables = _sqlite_tables(src)
        pg_tables = _pg_tables(pg, schema)
        mismatches: list[dict[str, Any]] = []
        missing_empty: list[str] = []
        missing_with_data: list[dict[str, Any]] = []
        matched = 0
        total_sqlite = 0
        total_postgres = 0

        for table in tables:
            sc = _sqlite_count(src, table)
            total_sqlite += sc
            if table not in pg_tables:
                entry = {
                    "table": table,
                    "sqlite": sc,
                    "postgres": None,
                    "status": "missing_in_pg",
                }
                if sc == 0:
                    missing_empty.append(table)
                else:
                    missing_with_data.append(entry)
                    mismatches.append(entry)
                continue
            pc = _pg_count(pg, schema, table)
            total_postgres += pc
            if sc != pc:
                mismatches.append(
                    {"table": table, "sqlite": sc, "postgres": pc, "status": "count_mismatch"}
                )
            else:
                matched += 1

        extra_pg = sorted(pg_tables - set(tables))
        # Empty tables missing in PG are warnings (lazy DDL). Data loss is a failure.
        passed = len(mismatches) == 0
        detail = (
            f"{matched}/{len(tables)} tables match; "
            f"sqlite_rows={total_sqlite} postgres_rows={total_postgres}; "
            f"mismatches={len(mismatches)} "
            f"missing_empty={len(missing_empty)} "
            f"missing_with_data={len(missing_with_data)} "
            f"extra_in_pg={len(extra_pg)}"
        )
        report.add(
            f"{label}: row counts",
            passed,
            detail,
            mismatches=mismatches[:50],
            missing_empty=missing_empty,
            missing_with_data=missing_with_data,
            extra_in_pg=extra_pg[:50],
            total_sqlite_rows=total_sqlite,
            total_postgres_rows=total_postgres,
        )
        for row in mismatches[:20]:
            _log(
                f"      * {row['table']}: sqlite={row['sqlite']} "
                f"postgres={row['postgres']} ({row['status']})"
            )
        if missing_empty:
            _log(
                f"      (warn) empty tables missing in PG: "
                f"{', '.join(missing_empty[:12])}"
                + ("..." if len(missing_empty) > 12 else "")
            )
        if len(mismatches) > 20:
            _log(f"      ... {len(mismatches) - 20} more")
    finally:
        src.close()


# ── 2. constraints / FK integrity ───────────────────────────────────────────


def check_constraints(report: Report, pg, schema: str, label: str) -> None:
    _log(f"\n== 2. Constraints / FK integrity: {label} ==")
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT constraint_type, COUNT(*)
            FROM information_schema.table_constraints
            WHERE table_schema = %s
              AND constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY', 'UNIQUE')
            GROUP BY constraint_type
            ORDER BY constraint_type
            """,
            (schema,),
        )
        counts = {r[0]: int(r[1]) for r in cur.fetchall()}

        cur.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            """,
            (schema,),
        )
        table_count = int(cur.fetchone()[0])

        cur.execute(
            """
            SELECT COUNT(*) FROM information_schema.table_constraints
            WHERE table_schema = %s AND constraint_type = 'PRIMARY KEY'
            """,
            (schema,),
        )
        pk_count = int(cur.fetchone()[0])

    pk_ok = pk_count >= table_count  # every base table should have a PK in this schema
    report.add(
        f"{label}: primary keys",
        pk_ok,
        f"tables={table_count} primary_keys={pk_count} "
        f"foreign_keys={counts.get('FOREIGN KEY', 0)} unique={counts.get('UNIQUE', 0)}",
        constraint_counts=counts,
        table_count=table_count,
    )

    # Orphan scan: for each FK, count child rows whose parent is missing.
    orphans: list[dict[str, Any]] = []
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT
                tc.table_name AS child_table,
                kcu.column_name AS child_column,
                ccu.table_name AS parent_table,
                ccu.column_name AS parent_column,
                tc.constraint_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = %s
            ORDER BY tc.table_name, kcu.ordinal_position
            """,
            (schema,),
        )
        fks = cur.fetchall()

    # Group multi-column FKs by constraint name (verify simple single-column FKs;
    # multi-column checked as AND of all columns).
    by_constraint: dict[str, list[Any]] = {}
    for row in fks:
        by_constraint.setdefault(row["constraint_name"], []).append(row)

    with pg.cursor() as cur:
        for cname, parts in by_constraint.items():
            child = parts[0]["child_table"]
            parent = parts[0]["parent_table"]
            join_pred = " AND ".join(
                f"c.{_qi(p['child_column'])} = p.{_qi(p['parent_column'])}"
                for p in parts
            )
            null_skip = " OR ".join(
                f"c.{_qi(p['child_column'])} IS NULL" for p in parts
            )
            sql = f"""
                SELECT COUNT(*) FROM {_qi(schema)}.{_qi(child)} c
                WHERE NOT ({null_skip})
                  AND NOT EXISTS (
                    SELECT 1 FROM {_qi(schema)}.{_qi(parent)} p
                    WHERE {join_pred}
                  )
            """
            try:
                cur.execute(sql)
                n = int(cur.fetchone()[0])
            except Exception as exc:  # noqa: BLE001
                pg.rollback()
                orphans.append(
                    {
                        "constraint": cname,
                        "child": child,
                        "parent": parent,
                        "orphans": -1,
                        "error": str(exc),
                    }
                )
                continue
            if n:
                orphans.append(
                    {
                        "constraint": cname,
                        "child": child,
                        "parent": parent,
                        "orphans": n,
                    }
                )

    report.add(
        f"{label}: FK orphan scan",
        len(orphans) == 0,
        f"fk_constraints={len(by_constraint)} orphan_groups={len(orphans)}",
        orphans=orphans[:30],
    )
    for row in orphans[:10]:
        _log(
            f"      * {row.get('child')} -> {row.get('parent')} "
            f"orphans={row.get('orphans')} ({row.get('constraint')})"
        )


# ── 3. insert / sequence tests ──────────────────────────────────────────────


def check_inserts_and_sequences(report: Report, pg, schema: str, label: str) -> None:
    _log(f"\n== 3. Insert / sequence smoke tests: {label} ==")

    # 3a. SERIAL / IDENTITY sequences (rare in this PMS - mostly TEXT UUIDs)
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT
                n.nspname AS schema_name,
                c.relname AS table_name,
                a.attname AS column_name,
                pg_get_serial_sequence(
                    quote_ident(n.nspname) || '.' || quote_ident(c.relname),
                    a.attname
                ) AS seq_name
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a
              ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
            WHERE n.nspname = %s
              AND c.relkind = 'r'
              AND pg_get_serial_sequence(
                    quote_ident(n.nspname) || '.' || quote_ident(c.relname),
                    a.attname
                  ) IS NOT NULL
            """,
            (schema,),
        )
        sequences = cur.fetchall()

    if not sequences:
        report.add(
            f"{label}: serial sequences",
            True,
            "no SERIAL/IDENTITY columns (PKs are TEXT UUIDs - expected)",
        )
    else:
        seq_ok = True
        details: list[str] = []
        with pg.cursor() as cur:
            for row in sequences:
                sch, table, col, seq = (
                    row["schema_name"],
                    row["table_name"],
                    row["column_name"],
                    row["seq_name"],
                )
                cur.execute(
                    f"SELECT COALESCE(MAX({_qi(col)}), 0) FROM {_qi(sch)}.{_qi(table)}"
                )
                max_id = int(cur.fetchone()[0])
                cur.execute("SELECT last_value, is_called FROM " + seq)
                last_value, is_called = cur.fetchone()
                effective = int(last_value) if is_called else 0
                # Sequence must be at least MAX(id) so nextval won't collide.
                good = effective >= max_id
                seq_ok = seq_ok and good
                details.append(
                    f"{seq}: last={effective} max({table}.{col})={max_id} ok={good}"
                )
                if good and max_id > 0:
                    # Exercise nextval without inserting into app tables.
                    cur.execute("SELECT nextval(%s::regclass)", (seq,))
                    nxt = int(cur.fetchone()[0])
                    cur.execute("SELECT setval(%s::regclass, %s, true)", (seq, effective))
                    if nxt <= max_id:
                        seq_ok = False
                        details.append(f"{seq}: nextval={nxt} would collide with max={max_id}")
        pg.rollback()
        report.add(
            f"{label}: serial sequences",
            seq_ok,
            "; ".join(details[:8]) or "checked",
            sequences=details,
        )

    # 3b. TEXT UUID / key insert on a key table (rollback - no lasting side effects)
    insert_ok = False
    insert_detail = "no suitable key table found for insert test"
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            """,
            (schema,),
        )
        have = {r[0] for r in cur.fetchall()}

        if schema == "webclaw" and "webclaw_config" in have:
            test_id = f"__verify_{uuid.uuid4().hex[:12]}"
            try:
                cur.execute(
                    f"""
                    INSERT INTO {_qi(schema)}.webclaw_config (key, value, updated_at)
                    VALUES (%s, %s, %s)
                    """,
                    (test_id, '{"verify": true}', datetime.now(timezone.utc)),
                )
                cur.execute(
                    f"SELECT key, value FROM {_qi(schema)}.webclaw_config WHERE key = %s",
                    (test_id,),
                )
                row = cur.fetchone()
                insert_ok = row is not None and row[0] == test_id
                insert_detail = (
                    f"inserted+selected webclaw_config.key={test_id} (rolled back)"
                )
            except Exception as exc:  # noqa: BLE001
                insert_ok = False
                insert_detail = f"webclaw_config insert failed: {exc}"
            finally:
                pg.rollback()

        elif schema == "public" and "uom" in have:
            # uom typically: id TEXT PK, name, …
            cur.execute(
                """
                SELECT column_name, is_nullable, column_default, data_type
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = 'uom'
                ORDER BY ordinal_position
                """,
                (schema,),
            )
            cols = cur.fetchall()
            col_names = [c[0] for c in cols]
            if "id" in col_names and "name" in col_names:
                test_id = f"__verify_{uuid.uuid4().hex[:12]}"
                try:
                    # Build minimal insert: id + name + NOT NULL columns with defaults skipped
                    required = [
                        c[0]
                        for c in cols
                        if c[1] == "NO" and c[2] is None and c[0] not in ("id", "name")
                    ]
                    if not required:
                        cur.execute(
                            f"INSERT INTO {_qi(schema)}.uom (id, name) VALUES (%s, %s)",
                            (test_id, f"verify-{test_id}"),
                        )
                        cur.execute(
                            f"SELECT id, name FROM {_qi(schema)}.uom WHERE id = %s",
                            (test_id,),
                        )
                        row = cur.fetchone()
                        insert_ok = row is not None and row[0] == test_id
                        insert_detail = (
                            f"inserted+selected uom.id={test_id} (rolled back)"
                        )
                        # Collision check: same PK must fail
                        try:
                            cur.execute(
                                f"INSERT INTO {_qi(schema)}.uom (id, name) VALUES (%s, %s)",
                                (test_id, "dup"),
                            )
                            insert_ok = False
                            insert_detail += "; duplicate PK unexpectedly allowed"
                        except Exception:
                            insert_ok = insert_ok and True
                            insert_detail += "; duplicate PK rejected as expected"
                            pg.rollback()
                    else:
                        insert_detail = (
                            f"uom has extra NOT NULL cols {required}; skipped insert"
                        )
                        insert_ok = True  # not a failure - schema too strict for generic insert
                except Exception as exc:  # noqa: BLE001
                    insert_ok = False
                    insert_detail = f"uom insert failed: {exc}"
                finally:
                    pg.rollback()
            else:
                insert_detail = "uom table lacks id/name columns"
                insert_ok = True
        else:
            # Generic: pick first empty-ish TEXT PK table with only id + nullable cols
            insert_ok = True
            insert_detail = (
                f"skipped table-specific insert for schema={schema} "
                f"(no webclaw_config/uom); UUID PK model needs no sequence"
            )

    report.add(f"{label}: test INSERT", insert_ok, insert_detail)


# ── 4. common application queries ───────────────────────────────────────────


def _time_query_sqlite(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> tuple[list, float]:
    t0 = time.perf_counter()
    rows = conn.execute(sql, params).fetchall()
    elapsed = time.perf_counter() - t0
    return [tuple(r) for r in rows], elapsed


def _time_query_pg(pg, sql: str, params: tuple = ()) -> tuple[list, float]:
    with pg.cursor() as cur:
        t0 = time.perf_counter()
        cur.execute(sql, params)
        rows = cur.fetchall()
        elapsed = time.perf_counter() - t0
    # DictRow -> tuple by positional index (stable column order)
    out = []
    for r in rows:
        out.append(tuple(r[i] for i in range(len(r))))
    return out, elapsed


def check_app_queries(
    report: Report,
    sqlite_path: Path,
    pg,
    schema: str,
    label: str,
) -> None:
    _log(f"\n== 4. Sample application queries: {label} ==")
    if not sqlite_path.is_file():
        report.add(f"{label}: app queries", False, f"missing {sqlite_path}")
        return

    src = sqlite3.connect(str(sqlite_path))
    src.row_factory = None
    try:
        with pg.cursor() as cur:
            cur.execute(f"SET search_path TO {_qi(schema)}")
        pg.commit()

        queries: list[tuple[str, str, tuple]] = []
        tables = set(_sqlite_tables(src)) & _pg_tables(pg, schema)

        if schema == "webclaw":
            if "webclaw_user" in tables:
                queries.append(
                    (
                        "list users",
                        "SELECT id, email, full_name FROM webclaw_user ORDER BY email LIMIT 20",
                        (),
                    )
                )
            if "webclaw_user_role" in tables and "webclaw_role" in tables:
                queries.append(
                    (
                        "user role join",
                        """
                        SELECT u.email, r.code, r.name
                        FROM webclaw_user u
                        JOIN webclaw_user_role ur ON ur.user_id = u.id
                        JOIN webclaw_role r ON r.id = ur.role_id
                        ORDER BY u.email, r.code
                        LIMIT 50
                        """,
                        (),
                    )
                )
            if "webclaw_role_grant" in tables and "webclaw_permission" in tables:
                queries.append(
                    (
                        "role grants",
                        """
                        SELECT r.code, p.name
                        FROM webclaw_role r
                        JOIN webclaw_role_grant g ON g.role_id = r.id
                        JOIN webclaw_permission p ON p.id = g.permission_id
                        ORDER BY r.code, p.name
                        LIMIT 50
                        """,
                        (),
                    )
                )
            if "webclaw_session" in tables:
                queries.append(
                    (
                        "active sessions count",
                        "SELECT COUNT(*) FROM webclaw_session",
                        (),
                    )
                )
        else:  # public / ERP
            if "company" in tables:
                queries.append(
                    (
                        "list companies",
                        "SELECT id, name FROM company ORDER BY name LIMIT 20",
                        (),
                    )
                )
            if "hospitalityclaw_hotel" in tables:
                queries.append(
                    (
                        "list hotels",
                        """
                        SELECT id, name, tenant_id, timezone
                        FROM hospitalityclaw_hotel
                        ORDER BY name
                        LIMIT 20
                        """,
                        (),
                    )
                )
            if "hospitalityclaw_reservation" in tables:
                queries.append(
                    (
                        "reservations by status",
                        """
                        SELECT status, COUNT(*) AS n
                        FROM hospitalityclaw_reservation
                        GROUP BY status
                        ORDER BY status
                        """,
                        (),
                    )
                )
            if "uom" in tables:
                queries.append(
                    (
                        "list uom",
                        "SELECT id, name FROM uom ORDER BY name LIMIT 30",
                        (),
                    )
                )
            if "account" in tables:
                queries.append(
                    (
                        "account sample",
                        "SELECT id, name, account_type FROM account ORDER BY name LIMIT 20",
                        (),
                    )
                )

        if not queries:
            report.add(f"{label}: app queries", True, "no sample queries applicable")
            return

        failures: list[str] = []
        timings: list[dict[str, Any]] = []
        for name, sql, params in queries:
            try:
                sq_rows, sq_ms = _time_query_sqlite(src, sql, params)
                # Qualify-free SQL works on PG when search_path is set.
                pg_rows, pg_ms = _time_query_pg(pg, sql, params)
            except Exception as exc:  # noqa: BLE001
                pg.rollback()
                failures.append(f"{name}: query error: {exc}")
                continue

            # Normalize for comparison: stringify; sort so collation differences
            # (e.g. Unicode in ORDER BY name) do not false-fail.
            def _norm(rows: list[tuple]) -> list[tuple]:
                out = []
                for row in rows:
                    vals = tuple(row[i] for i in range(len(row)))
                    out.append(tuple("" if v is None else str(v) for v in vals))
                return sorted(out)

            n_sq, n_pg = _norm(sq_rows), _norm(pg_rows)
            same = n_sq == n_pg
            timings.append(
                {
                    "query": name,
                    "sqlite_rows": len(sq_rows),
                    "postgres_rows": len(pg_rows),
                    "sqlite_ms": round(sq_ms * 1000, 2),
                    "postgres_ms": round(pg_ms * 1000, 2),
                    "match": same,
                }
            )
            if not same:
                failures.append(
                    f"{name}: row mismatch sqlite={len(sq_rows)} pg={len(pg_rows)} "
                    f"or value formatting differs"
                )
            _log(
                f"      * {name}: match={same} "
                f"sqlite={len(sq_rows)}/{timings[-1]['sqlite_ms']}ms "
                f"postgres={len(pg_rows)}/{timings[-1]['postgres_ms']}ms"
            )

        report.add(
            f"{label}: app queries",
            len(failures) == 0,
            f"{len(queries) - len(failures)}/{len(queries)} queries matched"
            + (f"; {failures[0]}" if failures else ""),
            timings=timings,
            failures=failures,
        )
    finally:
        src.close()


# ── checklist printer ───────────────────────────────────────────────────────


CHECKLIST = """
Manual verification checklist (optional, after the script passes)
----------------------------------------------------------------
[ ] pgAdmin: open schema webclaw + public; spot-check webclaw_user / company
[ ] App .env: DB_TYPE=postgres in webclaw/api/.env and erpclaw-engine/.env
[ ] Restart API; login with a migrated user email/password
[ ] Open a hotel property; confirm rooms/reservations load
[ ] SuperAdmin -> roles/permissions list shows migrated grants
[ ] Create a new reservation (or draft) and confirm it persists after refresh
[ ] Night-audit / status endpoints return 200 (not 400/500)
[ ] No SQLite file locks required for day-to-day API use
"""


# ── main ────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify SQLite -> PostgreSQL migration for PMS"
    )
    parser.add_argument("--webclaw-sqlite", type=Path, default=None)
    parser.add_argument("--erp-sqlite", type=Path, default=None)
    parser.add_argument("--pg-url", default=None)
    parser.add_argument("--only-webclaw", action="store_true")
    parser.add_argument("--only-erp", action="store_true")
    parser.add_argument(
        "--json-report",
        type=Path,
        default=None,
        help="Write machine-readable report JSON to this path",
    )
    parser.add_argument(
        "--skip-insert",
        action="store_true",
        help="Skip INSERT smoke tests",
    )
    parser.add_argument(
        "--skip-queries",
        action="store_true",
        help="Skip sample application query comparisons",
    )
    args = parser.parse_args()

    _load_env_file(ROOT / "webclaw" / "api" / ".env")
    _load_env_file(ROOT / "erpclaw-engine" / ".env")
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    if str(SETUP) not in sys.path:
        sys.path.insert(0, str(SETUP))

    webclaw_sqlite = args.webclaw_sqlite or _resolve_workspace_path(
        os.environ.get("WEBCLAW_DB_PATH"), "data", "webclaw.sqlite"
    )
    erp_sqlite = args.erp_sqlite or _resolve_workspace_path(
        os.environ.get("ERPCLAW_DB_PATH"), "data", "data.sqlite"
    )
    pg_url = args.pg_url or _postgres_url_from_env()

    do_webclaw = not args.only_erp
    do_erp = not args.only_webclaw

    report = Report(
        started_at=datetime.now(timezone.utc).isoformat(),
        postgres=_redact_url(pg_url),
    )

    _log("SQLite -> PostgreSQL verification")
    _log(f"  webclaw sqlite : {webclaw_sqlite}")
    _log(f"  erp sqlite     : {erp_sqlite}")
    _log(f"  postgres       : {_redact_url(pg_url)}")

    pg = _pg_connect(pg_url)
    try:
        if do_erp:
            check_counts(report, erp_sqlite, pg, "public", "ERP/public")
            check_constraints(report, pg, "public", "ERP/public")
            if not args.skip_insert:
                check_inserts_and_sequences(report, pg, "public", "ERP/public")
            if not args.skip_queries:
                check_app_queries(report, erp_sqlite, pg, "public", "ERP/public")

        if do_webclaw:
            check_counts(report, webclaw_sqlite, pg, "webclaw", "WebClaw")
            check_constraints(report, pg, "webclaw", "WebClaw")
            if not args.skip_insert:
                check_inserts_and_sequences(report, pg, "webclaw", "WebClaw")
            if not args.skip_queries:
                check_app_queries(report, webclaw_sqlite, pg, "webclaw", "WebClaw")
    finally:
        pg.close()

    _log("\n== Summary ==")
    passed = sum(1 for c in report.checks if c.passed)
    failed = sum(1 for c in report.checks if not c.passed)
    _log(f"  checks passed={passed} failed={failed}")
    for c in report.checks:
        if not c.passed:
            _log(f"  FAIL -> {c.name}: {c.detail}")

    _log(CHECKLIST)

    if args.json_report:
        payload = {
            "started_at": report.started_at,
            "postgres": report.postgres,
            "ok": report.ok,
            "checks": [asdict(c) for c in report.checks],
        }
        args.json_report.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        _log(f"  wrote report: {args.json_report}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
