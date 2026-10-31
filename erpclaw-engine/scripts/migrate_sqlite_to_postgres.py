#!/usr/bin/env python3
"""Migrate PMS data from SQLite into PostgreSQL.

This project uses two SQLite files (not a single ``sqlite.db``):

* ``data/webclaw.sqlite``  -> PostgreSQL schema ``webclaw``
* ``data/data.sqlite``     -> PostgreSQL schema ``public``

Run schema creation first (or pass ``--init-schema``):

    python erpclaw-engine/scripts/init_postgres.py
    python erpclaw-engine/scripts/migrate_sqlite_to_postgres.py

Credentials come from ``webclaw/api/.env`` (``DB_HOST``, ``DB_PORT``,
``DB_NAME``, ``DB_USER``, ``DB_PASSWORD``) or from ``--pg-url``.

Examples:

    python erpclaw-engine/scripts/migrate_sqlite_to_postgres.py --dry-run
    python erpclaw-engine/scripts/migrate_sqlite_to_postgres.py --truncate --batch-size 500
    python erpclaw-engine/scripts/migrate_sqlite_to_postgres.py --only-webclaw
    python erpclaw-engine/scripts/migrate_sqlite_to_postgres.py --only-erp
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "erpclaw-engine" / "scripts" / "erpclaw-setup" / "lib"
SETUP = ROOT / "erpclaw-engine" / "scripts" / "erpclaw-setup"

# SQLite internal / mirror tables we never copy.
_SKIP_TABLES = frozenset({
    "sqlite_sequence",
    "sqlite_stat1",
    "sqlite_stat4",
})

_ISO_TS = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)
_ISO_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


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

    from urllib.parse import quote

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


# ── SQLite helpers ──────────────────────────────────────────────────────────


def _sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [r[0] for r in rows if r[0] not in _SKIP_TABLES]


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _sqlite_fk_edges(conn: sqlite3.Connection, tables: list[str]) -> list[tuple[str, str]]:
    """Return (parent, child) edges: parent must be inserted before child."""
    known = set(tables)
    edges: list[tuple[str, str]] = []
    for table in tables:
        for row in conn.execute(f'PRAGMA foreign_key_list("{table}")'):
            parent = row[2]
            if parent in known and parent != table:
                edges.append((parent, table))
    return edges


def _topo_sort(tables: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """Kahn topological sort; break cycles by appending remaining nodes alphabetically."""
    indegree: dict[str, int] = {t: 0 for t in tables}
    children: dict[str, list[str]] = defaultdict(list)
    for parent, child in edges:
        if parent not in indegree or child not in indegree:
            continue
        children[parent].append(child)
        indegree[child] += 1

    queue = deque(sorted(t for t, d in indegree.items() if d == 0))
    ordered: list[str] = []
    while queue:
        node = queue.popleft()
        ordered.append(node)
        for child in children[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    remaining = [t for t in tables if t not in ordered]
    if remaining:
        _log(
            f"  warning: FK cycle or missing parents for {len(remaining)} table(s); "
            f"appending after ordered set: {', '.join(remaining[:12])}"
            + ("..." if len(remaining) > 12 else "")
        )
        # Prefer tables with fewer outbound FKs first among leftovers.
        ordered.extend(sorted(remaining))
    return ordered


def _sqlite_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _iter_sqlite_rows(
    conn: sqlite3.Connection,
    table: str,
    columns: list[str],
    batch_size: int,
):
    col_sql = ", ".join(f'"{c}"' for c in columns)
    cur = conn.execute(f'SELECT {col_sql} FROM "{table}"')
    while True:
        rows = cur.fetchmany(batch_size)
        if not rows:
            break
        yield rows


# ── PostgreSQL helpers ──────────────────────────────────────────────────────


def _pg_connect(url: str):
    import psycopg2
    from psycopg2.extras import DictCursor

    conn = psycopg2.connect(url, cursor_factory=DictCursor)
    conn.autocommit = False
    return conn


def _pg_existing_tables(pg, schema: str) -> set[str]:
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            """,
            (schema,),
        )
        return {r[0] for r in cur.fetchall()}


def _pg_column_meta(pg, schema: str, table: str) -> dict[str, dict[str, Any]]:
    """Map column_name → {data_type, udt_name, is_nullable}."""
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type, udt_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, table),
        )
        return {
            r[0]: {
                "data_type": r[1],
                "udt_name": r[2],
                "is_nullable": r[3] == "YES",
            }
            for r in cur.fetchall()
        }


def _pg_count(pg, schema: str, table: str) -> int:
    with pg.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
        return int(cur.fetchone()[0])


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _set_session_replication_role(pg, role: str) -> bool:
    """Skip FK/trigger enforcement during bulk load when permitted."""
    try:
        with pg.cursor() as cur:
            cur.execute(f"SET session_replication_role = {role}")
        pg.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — optional privilege
        pg.rollback()
        _log(f"  note: could not SET session_replication_role={role} ({exc})")
        return False


# ── Value conversion ────────────────────────────────────────────────────────


def _parse_timestamp(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # SQLite CURRENT_TIMESTAMP style without timezone
    match = _ISO_TS.match(text.replace(" ", "T", 1) if " " in text and "T" not in text else text)
    try:
        if match:
            normalized = text.replace(" ", "T", 1) if "T" not in text else text
            if normalized.endswith("Z"):
                normalized = normalized[:-1] + "+00:00"
            # Handle +HHMM without colon
            if re.search(r"[+-]\d{4}$", normalized):
                normalized = normalized[:-2] + ":" + normalized[-2:]
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        return datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError:
        return text  # let Postgres raise a clear error if truly invalid


def _parse_date(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    match = _ISO_DATE.match(text)
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return text
    return text


def _parse_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        from psycopg2.extras import Json

        return Json(value)
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    text = str(value).strip()
    if not text:
        return None
    from psycopg2.extras import Json

    try:
        return Json(json.loads(text))
    except (json.JSONDecodeError, TypeError):
        # Already-valid JSON text literals sometimes lack quotes incorrectly;
        # store as a JSON string.
        return Json(text)


def _to_bool(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    text = str(value).strip().lower()
    if text in ("1", "true", "t", "yes", "y", "on"):
        return True
    if text in ("0", "false", "f", "no", "n", "off"):
        return False
    return value


def _convert_value(value: Any, meta: dict[str, Any] | None) -> Any:
    if meta is None:
        return value
    udt = (meta.get("udt_name") or "").lower()
    data_type = (meta.get("data_type") or "").lower()

    if udt == "bool" or data_type == "boolean":
        return _to_bool(value)
    if udt in ("json", "jsonb") or data_type in ("json", "jsonb"):
        return _parse_json(value)
    if udt == "date" or data_type == "date":
        return _parse_date(value)
    if udt in ("timestamp", "timestamptz") or "timestamp" in data_type:
        return _parse_timestamp(value)
    if isinstance(value, bytes):
        # BYTEA / accidental binary
        return value
    return value


def _convert_row(
    row: tuple[Any, ...],
    columns: list[str],
    pg_meta: dict[str, dict[str, Any]],
) -> tuple[Any, ...]:
    out: list[Any] = []
    for col, value in zip(columns, row):
        out.append(_convert_value(value, pg_meta.get(col)))
    return tuple(out)


# ── Sequence reset ──────────────────────────────────────────────────────────


def _reset_sequences(pg, schema: str, tables: list[str]) -> int:
    """Advance SERIAL / IDENTITY sequences to MAX(pk) so next inserts succeed.

    Most PMS primary keys are TEXT UUIDs (no sequences). This still scans
    every table for owned sequences / identity columns and updates them.
    """
    updated = 0
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
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
            WHERE n.nspname = %s
              AND c.relkind = 'r'
              AND c.relname = ANY(%s)
              AND pg_get_serial_sequence(
                    quote_ident(n.nspname) || '.' || quote_ident(c.relname),
                    a.attname
                  ) IS NOT NULL
            """,
            (schema, tables),
        )
        sequences = cur.fetchall()

        for schema_name, table_name, column_name, seq_name in sequences:
            cur.execute(
                f"""
                SELECT setval(
                    %s::regclass,
                    COALESCE(
                        (SELECT MAX({_quote_ident(column_name)})
                         FROM {_quote_ident(schema_name)}.{_quote_ident(table_name)}),
                        0
                    ),
                    true
                )
                """,
                (seq_name,),
            )
            new_val = cur.fetchone()[0]
            _log(
                f"  sequence {seq_name} = {new_val} "
                f"(from {schema_name}.{table_name}.{column_name})"
            )
            updated += 1
    pg.commit()
    return updated


# ── Migration of one SQLite → one PG schema ─────────────────────────────────


def _migrate_database(
    sqlite_path: Path,
    pg,
    schema: str,
    *,
    batch_size: int,
    truncate: bool,
    dry_run: bool,
    include: set[str] | None,
    exclude: set[str] | None,
) -> dict[str, tuple[int, int, str]]:
    """Returns table → (sqlite_count, pg_count, status)."""
    results: dict[str, tuple[int, int, str]] = {}

    if not sqlite_path.is_file():
        _log(f"ERROR: SQLite file not found: {sqlite_path}")
        return results

    _log(f'\n=== Source: {sqlite_path} -> schema "{schema}" ===')
    src = sqlite3.connect(str(sqlite_path))
    src.row_factory = None
    try:
        tables = _sqlite_tables(src)
        if include:
            tables = [t for t in tables if t in include]
        if exclude:
            tables = [t for t in tables if t not in exclude]

        edges = _sqlite_fk_edges(src, tables)
        ordered = _topo_sort(tables, edges)
        existing_pg = _pg_existing_tables(pg, schema)
        _log(f"  SQLite tables selected: {len(ordered)}")
        _log(f"  PostgreSQL tables in {schema}: {len(existing_pg)}")

        if truncate and not dry_run:
            # Truncate children-first (reverse FK order) with CASCADE.
            to_truncate = [t for t in reversed(ordered) if t in existing_pg]
            if to_truncate:
                _log(f"  truncating {len(to_truncate)} table(s) in {schema} ...")
                with pg.cursor() as cur:
                    targets = ", ".join(
                        f"{_quote_ident(schema)}.{_quote_ident(t)}" for t in to_truncate
                    )
                    cur.execute(f"TRUNCATE TABLE {targets} RESTART IDENTITY CASCADE")
                pg.commit()

        for index, table in enumerate(ordered, start=1):
            src_count = _sqlite_count(src, table)
            if table not in existing_pg:
                _log(
                    f"  [{index}/{len(ordered)}] SKIP {table} "
                    f"(missing in PostgreSQL schema {schema}; src={src_count})"
                )
                results[table] = (src_count, -1, "missing_in_pg")
                continue

            pg_meta = _pg_column_meta(pg, schema, table)
            sqlite_cols = _sqlite_columns(src, table)
            # Only columns that exist on both sides, in SQLite order.
            columns = [c for c in sqlite_cols if c in pg_meta]
            skipped_cols = [c for c in sqlite_cols if c not in pg_meta]
            if skipped_cols:
                _log(
                    f"  [{index}/{len(ordered)}] {table}: "
                    f"ignoring SQLite-only columns {skipped_cols}"
                )
            if not columns:
                _log(f"  [{index}/{len(ordered)}] SKIP {table} (no overlapping columns)")
                results[table] = (src_count, 0, "no_columns")
                continue

            if dry_run:
                _log(
                    f"  [{index}/{len(ordered)}] DRY-RUN {table}: "
                    f"would copy {src_count} row(s), {len(columns)} column(s)"
                )
                results[table] = (src_count, src_count, "dry_run")
                continue

            if src_count == 0:
                _log(f"  [{index}/{len(ordered)}] {table}: 0 rows")
                results[table] = (0, _pg_count(pg, schema, table), "empty")
                continue

            col_list = ", ".join(_quote_ident(c) for c in columns)
            placeholders = ", ".join(["%s"] * len(columns))
            insert_sql = (
                f'INSERT INTO {_quote_ident(schema)}.{_quote_ident(table)} '
                f"({col_list}) VALUES ({placeholders})"
            )

            inserted = 0
            t0 = time.time()
            with pg.cursor() as cur:
                for batch in _iter_sqlite_rows(src, table, columns, batch_size):
                    converted = [
                        _convert_row(tuple(row), columns, pg_meta) for row in batch
                    ]
                    cur.executemany(insert_sql, converted)
                    inserted += len(converted)
                    if inserted % max(batch_size * 5, 1) == 0 or inserted == src_count:
                        _log(
                            f"  [{index}/{len(ordered)}] {table}: "
                            f"{inserted}/{src_count} rows ..."
                        )
            pg.commit()
            elapsed = time.time() - t0
            dst_count = _pg_count(pg, schema, table)
            status = "ok" if dst_count == src_count else "count_mismatch"
            _log(
                f"  [{index}/{len(ordered)}] {table}: "
                f"sqlite={src_count} postgres={dst_count} "
                f"({elapsed:.2f}s) [{status}]"
            )
            results[table] = (src_count, dst_count, status)

        if not dry_run:
            _log(f"  resetting sequences in schema {schema} ...")
            n = _reset_sequences(pg, schema, ordered)
            _log(f"  sequences updated: {n}")
    finally:
        src.close()

    return results


def _print_summary(label: str, results: dict[str, tuple[int, int, str]]) -> bool:
    if not results:
        _log(f"\n{label}: nothing migrated")
        return True
    ok = 0
    bad = 0
    skipped = 0
    _log(f"\n--- Verification: {label} ---")
    for table, (src, dst, status) in sorted(results.items()):
        if status in ("ok", "empty", "dry_run"):
            ok += 1
            mark = "OK"
        elif status in ("missing_in_pg", "no_columns"):
            skipped += 1
            mark = "SKIP"
        else:
            bad += 1
            mark = "FAIL"
        dst_disp = "n/a" if dst < 0 else str(dst)
        _log(f"  [{mark}] {table}: sqlite={src} postgres={dst_disp} ({status})")
    _log(f"  totals: ok={ok} skipped={skipped} failed={bad}")
    return bad == 0


def _maybe_init_schema() -> None:
    init_path = ROOT / "erpclaw-engine" / "scripts" / "init_postgres.py"
    _log(f"Running schema init: {init_path}")
    import runpy

    # init_postgres.main() honors argv; force a clean execute.
    old_argv = sys.argv
    try:
        sys.argv = [str(init_path)]
        runpy.run_path(str(init_path), run_name="__main__")
    finally:
        sys.argv = old_argv


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate webclaw.sqlite + data.sqlite into PostgreSQL"
    )
    parser.add_argument(
        "--webclaw-sqlite",
        type=Path,
        default=None,
        help="Path to webclaw SQLite (default: WEBCLAW_DB_PATH / data/webclaw.sqlite)",
    )
    parser.add_argument(
        "--erp-sqlite",
        type=Path,
        default=None,
        help="Path to ERP/hospitality SQLite (default: ERPCLAW_DB_PATH / data/data.sqlite)",
    )
    parser.add_argument(
        "--pg-url",
        default=None,
        help="PostgreSQL URL (default: DB_* / ERPCLAW_DB_URL from .env)",
    )
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="TRUNCATE target tables (CASCADE) before insert",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan and count only; do not write to PostgreSQL",
    )
    parser.add_argument(
        "--init-schema",
        action="store_true",
        help="Run init_postgres.py before migrating",
    )
    parser.add_argument(
        "--only-webclaw",
        action="store_true",
        help="Migrate only webclaw.sqlite -> schema webclaw",
    )
    parser.add_argument(
        "--only-erp",
        action="store_true",
        help="Migrate only data.sqlite -> schema public",
    )
    parser.add_argument(
        "--include",
        nargs="*",
        default=None,
        help="Optional whitelist of table names",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=None,
        help="Optional blacklist of table names",
    )
    parser.add_argument(
        "--no-replication-role",
        action="store_true",
        help="Do not SET session_replication_role=replica during load",
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

    include = set(args.include) if args.include else None
    exclude = set(args.exclude) if args.exclude else None

    do_webclaw = not args.only_erp
    do_erp = not args.only_webclaw

    _log("SQLite -> PostgreSQL migration")
    _log(f"  webclaw sqlite : {webclaw_sqlite}")
    _log(f"  erp sqlite     : {erp_sqlite}")
    _log(f"  postgres       : {_redact_url(pg_url)}")
    _log(f"  batch size     : {args.batch_size}")
    _log(f"  truncate       : {args.truncate}")
    _log(f"  dry-run        : {args.dry_run}")

    if args.init_schema and not args.dry_run:
        _maybe_init_schema()

    pg = _pg_connect(pg_url)
    replication = False
    try:
        if not args.dry_run and not args.no_replication_role:
            replication = _set_session_replication_role(pg, "replica")
            if replication:
                _log("  session_replication_role=replica (FK checks deferred)")

        all_ok = True
        web_results: dict[str, tuple[int, int, str]] = {}
        erp_results: dict[str, tuple[int, int, str]] = {}

        if do_erp:
            with pg.cursor() as cur:
                cur.execute("SET search_path TO public")
            pg.commit()
            erp_results = _migrate_database(
                erp_sqlite,
                pg,
                "public",
                batch_size=args.batch_size,
                truncate=args.truncate,
                dry_run=args.dry_run,
                include=include,
                exclude=exclude,
            )
            all_ok = _print_summary("ERP / hospitality (public)", erp_results) and all_ok

        if do_webclaw:
            with pg.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS webclaw")
                cur.execute("SET search_path TO webclaw")
            pg.commit()
            web_results = _migrate_database(
                webclaw_sqlite,
                pg,
                "webclaw",
                batch_size=args.batch_size,
                truncate=args.truncate,
                dry_run=args.dry_run,
                include=include,
                exclude=exclude,
            )
            all_ok = _print_summary("WebClaw (webclaw)", web_results) and all_ok

        return 0 if all_ok else 1
    finally:
        if replication:
            try:
                _set_session_replication_role(pg, "origin")
            except Exception:  # noqa: BLE001
                pass
        pg.close()


if __name__ == "__main__":
    raise SystemExit(main())
