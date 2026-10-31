"""PostgreSQL access for WebClaw.

The WebClaw **platform** (auth, sessions, plans, billing, audit, tickets)
requires PostgreSQL: set ``DB_TYPE=postgres``. Credentials come from
``DB_HOST``, ``DB_PORT``, ``DB_NAME``, ``DB_USER``, and ``DB_PASSWORD``
(host defaults to localhost, port to 5432). An explicit ``WEBCLAW_DB_URL``
still wins.

Call sites keep using ``?`` placeholders and ``conn.execute(sql, params)`` —
the Postgres facade rewrites ``?`` → ``%s`` and SQLite ``datetime(...)``
idioms leftover in application SQL.

Tenant filtering belongs in this layer so skill scripts and API routes do
not each invent a WHERE clause. Use :func:`tenant_where` / :class:`TenantQuery`.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Iterable, Protocol, Sequence, runtime_checkable

from env_loader import (
    load_api_env,
    postgres_url_from_env,
    require_platform_postgres,
    resolve_db_type,
    resolve_webclaw_db_path,
)

load_api_env()

DEFAULT_WEBCLAW_DB = resolve_webclaw_db_path()


@runtime_checkable
class DbConn(Protocol):
    """Shared connection surface for PostgresFacade and wrappers.

    Prefer this over driver-specific types in hints. ``execute(sql, params)``
    uses ``?`` placeholders (rewritten to ``%s`` for PostgreSQL).
    """

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


def get_dialect() -> str:
    return resolve_db_type()


_DT_NOW_OFFSET = re.compile(
    r"datetime\(\s*'now'\s*,\s*'([+-]\d+)\s+days?'\s*\)",
    re.IGNORECASE,
)
_DT_NOW = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)
_DT_COL = re.compile(
    r"datetime\(\s*((?:[A-Za-z_][\w]*|COALESCE\([^)]+\)))\s*\)",
    re.IGNORECASE,
)


def adapt_runtime_sql(sql: str) -> str:
    """Rewrite leftover SQLite-style runtime SQL for PostgreSQL.

    Handles ``datetime('now')``, ``datetime('now', '+N days')``, and
    ``datetime(column)`` / ``datetime(COALESCE(...))``.
    Placeholders stay as ``?`` until :func:`_qmark_to_pyformat`.
    """
    if get_dialect() != "postgresql":
        raise RuntimeError(f"PostgreSQL required (got dialect={get_dialect()!r})")

    def _offset(match: re.Match[str]) -> str:
        n = int(match.group(1))
        if n >= 0:
            return f"(CURRENT_TIMESTAMP + INTERVAL '{n} days')"
        return f"(CURRENT_TIMESTAMP - INTERVAL '{abs(n)} days')"

    out = _DT_NOW_OFFSET.sub(_offset, sql)
    out = _DT_NOW.sub("CURRENT_TIMESTAMP", out)
    out = _DT_COL.sub(r"(\1)::timestamptz", out)
    return out


def is_missing_relation(exc: BaseException) -> bool:
    """True when ``exc`` looks like a missing table/column (either dialect)."""
    msg = str(exc).lower()
    return (
        "no such table" in msg
        or "no such column" in msg
        or "undefined table" in msg
        or "undefined column" in msg
        or "does not exist" in msg
        or "undefinedcolumn" in msg.replace(" ", "")
    )


def list_relations(conn) -> set[str]:
    """Table and view names in the active schema."""
    try:
        rows = conn.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = current_schema()
            UNION
            SELECT table_name
            FROM information_schema.views
            WHERE table_schema = current_schema()
            """
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        _safe_rollback(conn)
        return set()


def _adapt_ddl_for_active_dialect(ddl: str) -> str:
    """Rewrite SQLite DDL for PostgreSQL when that backend is selected."""
    if get_dialect() != "postgresql":
        return ddl
    import sys

    from env_loader import resolve_erpclaw_lib_dir

    lib = resolve_erpclaw_lib_dir()
    if lib and lib not in sys.path:
        sys.path.insert(0, lib)
    try:
        from erpclaw_lib.pg_ddl import adapt_ddl_for_postgres

        return adapt_ddl_for_postgres(ddl)
    except ImportError:
        return (
            ddl.replace("datetime('now')", "CURRENT_TIMESTAMP")
            .replace('datetime("now")', "CURRENT_TIMESTAMP")
            .replace("date('now')", "CURRENT_DATE")
        )


def _split_ddl_statements(script: str) -> list[str]:
    """Split a DDL script into executable statements (semicolon-delimited)."""
    parts: list[str] = []
    buf: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buf.append(line)
        if ";" in line:
            chunk = "\n".join(buf).strip()
            buf = []
            for piece in chunk.split(";"):
                piece = piece.strip()
                if piece:
                    parts.append(piece)
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def run_ddl(conn, ddl: str) -> None:
    """Apply ``CREATE TABLE IF NOT EXISTS``-style DDL on PostgreSQL."""
    sql = _adapt_ddl_for_active_dialect(ddl)
    for stmt in _split_ddl_statements(sql):
        try:
            conn.execute(stmt)
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            msg = str(exc).lower()
            if (
                "already exists" in msg
                or "duplicate" in msg
                or "no such column" in msg
            ):
                continue
            raise
    try:
        conn.commit()
    except Exception:
        pass


def _safe_rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:
        pass


def table_columns(conn, table: str) -> set[str]:
    """Column names for ``table`` from Postgres ``information_schema``."""
    try:
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = ?
            """,
            (table,),
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        _safe_rollback(conn)
        return set()


def table_exists(conn, table: str) -> bool:
    """Return whether ``table`` exists in the active Postgres schema."""
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = ?
            """,
            (table,),
        ).fetchone()
        return row is not None
    except Exception:
        _safe_rollback(conn)
        return False


def _is_postgres_url(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.strip().lower()
    return lowered.startswith("postgres://") or lowered.startswith("postgresql://")


class ConnectionFacade:
    """Common execute/commit/close surface for PostgreSQL facades."""

    def execute(self, sql: str, params: Sequence[Any] | None = None):
        raise NotImplementedError

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]):
        raise NotImplementedError

    def commit(self) -> None:
        raise NotImplementedError

    def rollback(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def _qmark_to_pyformat(sql: str) -> str:
    out = []
    in_str = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            if in_str and i + 1 < n and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_str = not in_str
            out.append(ch)
        elif ch == "%" and not in_str:
            out.append("%%")
        elif ch == "?" and not in_str:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class PostgresFacade(ConnectionFacade):
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] | None = None):
        cur = self._conn.cursor()
        adapted = _qmark_to_pyformat(adapt_runtime_sql(sql))
        if params is None:
            cur.execute(adapted)
        else:
            cur.execute(adapted, params)
        return cur

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]):
        cur = self._conn.cursor()
        adapted = _qmark_to_pyformat(adapt_runtime_sql(sql))
        cur.executemany(adapted, seq_of_params)
        return cur

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _prepare_postgres(raw, *, search_path: str = "webclaw") -> None:
    """Configure a Postgres connection for WebClaw or ERP/skill schemas.

    WebClaw tables live in schema ``webclaw`` (avoids colliding with ERP
    ``audit_log``). ERP / hospitality / skill tables live in ``public``.
    """
    import sys

    from env_loader import resolve_erpclaw_lib_dir

    lib = resolve_erpclaw_lib_dir()
    if lib and lib not in sys.path:
        sys.path.insert(0, lib)
    try:
        from erpclaw_lib.pg_ddl import install_read_adapters
        install_read_adapters(raw)
    except ImportError:
        pass
    cur = raw.cursor()
    try:
        path = (search_path or "public").strip() or "public"
        if path == "webclaw":
            # Serialize schema creation so parallel cold starts cannot race.
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext('webclaw:schema'))"
            )
            cur.execute("CREATE SCHEMA IF NOT EXISTS webclaw")
            # Prefer webclaw; fall back to public for rare cross-schema reads.
            cur.execute("SET search_path TO webclaw, public")
        else:
            cur.execute("SET search_path TO public")
        raw.commit()
    finally:
        cur.close()


def _prepare_webclaw_postgres(raw) -> None:
    """Backward-compatible alias: WebClaw schema search_path."""
    _prepare_postgres(raw, search_path="webclaw")


def connect(db_path: str | None = None, *, search_path: str = "webclaw"):
    """Open a WebClaw (or skill) database with the active dialect.

    On PostgreSQL, ``search_path`` selects the schema:
      - ``webclaw`` for auth/UI tables (default)
      - ``public`` for ERP / hospitality / skill data
    """
    require_platform_postgres()
    import psycopg2
    from psycopg2.extras import DictCursor

    url = None
    if _is_postgres_url(db_path):
        url = db_path.strip()
    else:
        for candidate in (
            os.environ.get("WEBCLAW_DB_URL"),
            os.environ.get("ERPCLAW_DB_URL"),
        ):
            if _is_postgres_url(candidate):
                url = candidate.strip()
                break
    if not url:
        url = postgres_url_from_env()
    if not url:
        raise RuntimeError(
            "DB_TYPE=postgres requires DB_HOST, DB_PORT, DB_NAME, "
            "DB_USER, and DB_PASSWORD (or WEBCLAW_DB_URL)"
        )
    last_exc = None
    delay = 0.5
    for attempt in range(1, 4):
        try:
            raw = psycopg2.connect(url, cursor_factory=DictCursor)
            _prepare_postgres(raw, search_path=search_path)
            return PostgresFacade(raw)
        except psycopg2.OperationalError as exc:
            last_exc = exc
            time.sleep(delay)
            delay *= 2
    raise last_exc


def tenant_where(sql: str, tenant_column: str = "tenant_id") -> str:
    """Append a parameterized tenant predicate. Caller supplies the UUID last.

    ``sql`` must already be parameterized. This helper only appends
    ``AND {column} = ?`` so it cannot inject a tenant from a string.
    """
    body = sql.rstrip().rstrip(";")
    lower = body.lower()
    if " where " in lower:
        return f"{body} AND {tenant_column} = ?"
    return f"{body} WHERE {tenant_column} = ?"


def scoped_execute(conn, sql: str, params: Sequence[Any], tenant_id: str,
                   tenant_column: str = "tenant_id"):
    """Run ``sql`` with a mandatory tenant predicate appended."""
    if not tenant_id:
        raise ValueError("tenant_id is required for scoped queries")
    scoped_sql = tenant_where(sql, tenant_column)
    return conn.execute(scoped_sql, (*params, tenant_id))


class TenantQuery:
    """Connection-bound helper that always appends the current tenant predicate.

    Prefer this (or ``conn.execute`` on a tenant-scoped connection) over
    hand-written ``AND tenant_id = ?`` at each call site.
    """

    def __init__(self, conn, tenant_id: str, tenant_column: str = "tenant_id"):
        if not tenant_id:
            raise ValueError("tenant_id is required for TenantQuery")
        self.conn = conn
        self.tenant_id = tenant_id
        self.tenant_column = tenant_column

    def execute(self, sql: str, params: Sequence[Any] = ()):
        return scoped_execute(
            self.conn, sql, params, self.tenant_id, self.tenant_column
        )
