"""Database connection helper for ERPClaw.

Provides a standard way to get a database connection with the correct
connection settings applied for the active dialect:

  - PostgreSQL (``DB_TYPE=postgres`` / ``ERPCLAW_DB_DIALECT=postgresql``):
    a psycopg2 connection with ``lock_timeout`` / ``statement_timeout`` set
    and a ``DictCursor`` so rows support both positional (``row[0]``) and
    key (``row['col']``) access — matching ``sqlite3.Row`` semantics. The
    returned wrapper exposes a SQLite-style ``conn.execute(sql, params)``
    API, translating ``?`` placeholders to psycopg2's ``%s``. Hotel / ERP
    tables live in schema ``public``.
Dialect is PostgreSQL (``DB_TYPE=postgres``). The URL is resolved from a
postgres ``db_path``, ``ERPCLAW_DB_URL``, or ``DB_HOST`` / ``DB_PORT`` /
``DB_NAME`` / ``DB_USER`` / ``DB_PASSWORD`` (host defaults to localhost,
port to 5432). SQLite is not a supported runtime.
"""
import os
import sqlite3
import stat
import time
from decimal import Decimal
from urllib.parse import quote

from erpclaw_lib.paths import db_default


# Default SQLite path, derived from ERPCLAW_HOME (ADR-0017). With ERPCLAW_HOME
# unset this equals os.path.expanduser("~/.openclaw/erpclaw/data.sqlite") exactly.
# The ERPCLAW_DB_URL / ERPCLAW_DB_PATH chain below remains the DB-location
# authority; this is only the default underneath it.
DEFAULT_DB_PATH = db_default()


def _normalize_dialect(raw: str | None) -> str:
    """Map ``postgres`` / ``pg`` onto the canonical ``postgresql`` name."""
    value = (raw or "").strip().lower()
    if value in ("postgres", "postgresql", "pg", ""):
        return "postgresql"
    return value


def get_dialect():
    """Return the configured database dialect (PostgreSQL).

    ``DB_TYPE`` wins when it is set. Unset means PostgreSQL. When ``DB_TYPE``
    is set, the canonical name is copied onto ``ERPCLAW_DB_DIALECT`` and
    ``WEBCLAW_DB_DIALECT`` for callers that still read those directly.
    """
    explicit = (os.environ.get("DB_TYPE") or "").strip()
    raw = explicit or os.environ.get("ERPCLAW_DB_DIALECT") or "postgresql"
    dialect = _normalize_dialect(raw)
    if explicit:
        os.environ["ERPCLAW_DB_DIALECT"] = dialect
        os.environ["WEBCLAW_DB_DIALECT"] = dialect
    return dialect


def _is_postgres_url(value) -> bool:
    if not value:
        return False
    lowered = str(value).strip().lower()
    return lowered.startswith("postgres://") or lowered.startswith("postgresql://")


def postgres_url_from_env():
    """Build a libpq URL from the discrete ``DB_*`` credential variables.

    Returns None when none of them are set. Host and port default to
    ``localhost`` and ``5432`` once any of the variables is present.
    """
    keys = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    if not any((os.environ.get(key) or "").strip() for key in keys):
        return None
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


def db_error_types():
    """Return DB-API exception classes for the active dialect.

    Returns a ``(missing_table_excs, db_error_base)`` tuple:
      - ``missing_table_excs``: tuple of exception classes raised when a
        table or relation does not exist. Tolerated on minimal installs
        where an optional table (e.g. an audit/compliance log) has not
        been created yet.
      - ``db_error_base``: the dialect's PEP 249 base error class, used to
        catch any *other* database failure so it can be surfaced rather
        than silently swallowed.

    Both sqlite3 and psycopg2 implement the PEP 249 DB-API but expose
    distinct exception classes, so callers must NOT hardcode ``sqlite3.*``
    when ``ERPCLAW_DB_DIALECT=postgresql``. On SQLite a missing table raises
    ``OperationalError`` ("no such table"); on PostgreSQL it raises
    ``psycopg2.errors.UndefinedTable`` (a ``ProgrammingError``,
    SQLSTATE 42P01). The missing-table classes subclass the base error
    class in both drivers, so callers must order their ``except`` clauses
    most-specific first.
    """
    if get_dialect() == "postgresql":
        import psycopg2
        from psycopg2 import errors as _pg_errors
        return (_pg_errors.UndefinedTable,), psycopg2.Error
    raise RuntimeError(
        "ERPClaw requires DB_TYPE=postgres "
        f"(got dialect={get_dialect()!r})."
    )


def setup_pragmas(conn):
    """Apply vendor-specific connection settings.

    For SQLite: WAL mode, FK enforcement, busy timeout.
    For PostgreSQL: lock timeout + statement timeout (via cursor for psycopg2
      compatibility). ``lock_timeout`` is the direct analogue of SQLite's
      ``busy_timeout`` (how long to wait for a lock before erroring);
      ``statement_timeout`` has no SQLite equivalent so it defaults to 0
      (unlimited), but the statement is still issued per the cross-DB plan.
      Both are overridable via ``ERPCLAW_PG_LOCK_TIMEOUT`` /
      ``ERPCLAW_PG_STATEMENT_TIMEOUT``.
    Supported backends are SQLite and PostgreSQL only (MySQL ruled out
    2026-08-11 — see erpclaw_lib/query.py's SUPPORTED_DIALECTS note).
    """
    dialect = get_dialect()
    if dialect == "postgresql":
        lock_timeout = os.environ.get("ERPCLAW_PG_LOCK_TIMEOUT", "5s")
        statement_timeout = os.environ.get("ERPCLAW_PG_STATEMENT_TIMEOUT", "0")
        cur = conn.cursor() if hasattr(conn, 'cursor') else conn
        try:
            cur.execute("SET lock_timeout = %s", (lock_timeout,))
            cur.execute("SET statement_timeout = %s", (statement_timeout,))
        finally:
            if cur is not conn:
                cur.close()


class _DecimalSum:
    """Custom SQLite aggregate: SUM using Python Decimal for precision.

    SQLite's built-in SUM uses IEEE 754 float, which can lose precision
    on financial amounts stored as TEXT. This aggregate sums values using
    Python's Decimal type and returns the result as TEXT.

    Usage in SQL: decimal_sum(column) instead of SUM(CAST(column AS REAL))
    """

    def __init__(self):
        self.total = Decimal("0")

    def step(self, value):
        if value is not None:
            self.total += Decimal(str(value))

    def finalize(self):
        return str(self.total)


class ConnectionWrapper:
    """Wrapper around sqlite3.Connection that allows setting custom attributes.

    Python 3.12+ disallows setting arbitrary attributes on sqlite3.Connection.
    This wrapper delegates all sqlite3 methods to the underlying connection
    while allowing custom attributes (e.g., conn.company_id) that ERPClaw
    skills use for naming series resolution.
    """

    def __init__(self, conn: sqlite3.Connection):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_tenant_col_cache", {})

    def _scope_sql(self, sql, params):
        from erpclaw_lib.sql_tenant import (
            apply_multi_property_predicate,
            apply_tenant_predicate,
            sqlite_has_group_column,
            sqlite_tenant_column,
        )
        from erpclaw_lib.tenant import (
            current_group_id,
            current_tenant_id,
            current_tenant_ids,
        )

        tid = getattr(self, "tenant_id", None) or current_tenant_id()
        cache = self._tenant_col_cache

        def column_for(table):
            if table not in cache:
                cache[table] = sqlite_tenant_column(self._conn, table)
            return cache[table]

        if tid:
            return apply_tenant_predicate(sql, params, tid, column_for)

        tids = current_tenant_ids()
        gid = current_group_id()
        if tids or gid:
            return apply_multi_property_predicate(
                sql,
                params,
                tids,
                column_for,
                group_id=gid,
                has_group_column=lambda t: sqlite_has_group_column(self._conn, t),
            )
        return sql, params

    def execute(self, sql, params=None):
        sql, params = self._scope_sql(sql, params)
        if params is None:
            return self._conn.execute(sql)
        return self._conn.execute(sql, params)

    def executemany(self, sql, seq_of_params):
        seq = list(seq_of_params)
        if not seq:
            return self._conn.executemany(sql, seq)
        scoped_sql, first = self._scope_sql(sql, seq[0])
        if scoped_sql == sql:
            return self._conn.executemany(sql, seq)
        rows = [first]
        for row in seq[1:]:
            _, bound = self._scope_sql(sql, row)
            rows.append(bound)
        return self._conn.executemany(scoped_sql, rows)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        try:
            setattr(self._conn, name, value)
        except AttributeError:
            object.__setattr__(self, name, value)

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, *args):
        return self._conn.__exit__(*args)

    def __call__(self, *args, **kwargs):
        return self._conn(*args, **kwargs)


def _qmark_to_pyformat(sql: str) -> str:
    """Translate SQLite qmark (``?``) placeholders to psycopg2 pyformat (``%s``).

    The codebase builds parameterized SQL with PyPika's ``QmarkParameter`` (``?``);
    psycopg2 expects ``%s``. Translation rules, in a single left-to-right scan
    that tracks single-quoted string literals:

      - A ``?`` OUTSIDE a string literal becomes ``%s``.
      - A ``?`` INSIDE a string literal is left untouched (it's data, not a
        placeholder).
      - Every literal ``%`` is doubled to ``%%`` — when params are supplied,
        psycopg2 runs its own %-substitution over the whole query string, so a
        bare ``%`` (e.g. inside a ``LIKE '%foo%'``) would be mis-parsed.

    Only call this when params are actually being passed; with no params
    psycopg2 does not %-process the query, so ``%`` must be left alone.
    """
    out = []
    in_str = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            # A doubled '' is an escaped quote inside a string literal; emit both
            # and stay in the same state.
            if in_str and i + 1 < n and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_str = not in_str
            out.append(ch)
        elif ch == "%":
            out.append("%%")
        elif ch == "?" and not in_str:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class PgConnectionWrapper:
    """SQLite-style facade over a psycopg2 connection.

    Domain code is written against ``sqlite3.Connection``: it calls
    ``conn.execute(sql, params)`` directly (sqlite3 returns a cursor),
    iterates rows, and sometimes sets bookkeeping attributes such as
    ``conn.company_id``. psycopg2 connections have none of that — you go
    through ``conn.cursor()`` and use ``%s`` placeholders.

    This wrapper bridges the gap so ``get_connection()`` returns a drop-in
    object for both backends:

      - ``execute`` / ``executemany`` open a cursor (``DictCursor`` via the
        connection's ``cursor_factory``), translate ``?`` → ``%s``, run the
        statement, and return the cursor (which supports ``fetchone`` /
        ``fetchall`` and yields rows that index by position and by name).
      - ``commit`` / ``rollback`` / ``close`` / ``cursor`` and the context
        manager delegate to the underlying psycopg2 connection, whose
        ``with`` semantics (commit on success, rollback on error, no close)
        already match sqlite3.
      - Arbitrary attributes set on the wrapper that psycopg2 rejects are
        stored on the wrapper itself (mirrors ``ConnectionWrapper``).

    Note: the ``decimal_sum`` aggregate is registered as a persistent SQL
    aggregate by :func:`_ensure_pg_decimal_sum` during ``get_connection`` (it
    cannot be a per-connection Python aggregate as on SQLite). This wrapper
    only covers connection establishment + the execute/row seam.
    """

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_tenant_col_cache", {})

    def _scope_sql(self, sql, params):
        from erpclaw_lib.sql_tenant import (
            apply_multi_property_predicate,
            apply_tenant_predicate,
            sqlite_has_group_column,
            sqlite_tenant_column,
        )
        from erpclaw_lib.tenant import (
            current_group_id,
            current_tenant_id,
            current_tenant_ids,
        )

        tid = getattr(self, "tenant_id", None) or current_tenant_id()
        cache = self._tenant_col_cache

        def column_for(table):
            if table not in cache:
                cache[table] = sqlite_tenant_column(self._conn, table)
            return cache[table]

        if tid:
            return apply_tenant_predicate(sql, params, tid, column_for)

        tids = current_tenant_ids()
        gid = current_group_id()
        if tids or gid:
            return apply_multi_property_predicate(
                sql,
                params,
                tids,
                column_for,
                group_id=gid,
                has_group_column=lambda t: sqlite_has_group_column(self._conn, t),
            )
        return sql, params

    def execute(self, sql, params=None):
        sql, params = self._scope_sql(sql, params)
        cur = self._conn.cursor()
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(_qmark_to_pyformat(sql), params)
        return cur

    def executemany(self, sql, seq_of_params):
        seq = list(seq_of_params)
        if seq:
            original = sql
            sql, first = self._scope_sql(original, seq[0])
            rest = [self._scope_sql(original, row)[1] for row in seq[1:]]
            seq = [first, *rest]
        cur = self._conn.cursor()
        cur.executemany(_qmark_to_pyformat(sql), seq)
        return cur

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        try:
            setattr(self._conn, name, value)
        except (AttributeError, TypeError):
            object.__setattr__(self, name, value)

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, *args):
        return self._conn.__exit__(*args)


def _ensure_pg_decimal_sum(conn) -> None:
    """Register the ``decimal_sum(text)`` aggregate on PostgreSQL if absent.

    On SQLite the aggregate is a per-connection Python registration
    (``conn.create_aggregate`` in :func:`get_connection`); PostgreSQL needs a
    persistent SQL aggregate object instead. This mirrors that registration so
    both backends expose ``decimal_sum(col)`` — financial sums over TEXT-stored
    Decimal amounts (cross-DB add-on C).

    The aggregate sums each value as ``numeric`` (exact, no float drift) and
    returns the total as TEXT, matching the SQLite ``_DecimalSum.finalize``
    contract so call sites can keep doing ``to_decimal(str(row["total"]))``.
    The common path performs only a catalog lookup. Installation is serialized
    with a transaction-scoped advisory lock so parallel application requests
    cannot race while updating PostgreSQL's function catalog.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT to_regprocedure('public.decimal_sum(text)') IS NOT NULL"
        )
        if cur.fetchone()[0]:
            conn.commit()
            return

        # Every skill action opens its own connection/subprocess. On a cold
        # start many actions can arrive together, so serialize the one-time DDL
        # and check again after acquiring the lock.
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext('erpclaw:decimal_sum'))"
        )
        cur.execute(
            "SELECT to_regprocedure('public.decimal_sum(text)') IS NOT NULL"
        )
        if cur.fetchone()[0]:
            conn.commit()
            return

        cur.execute(
            """
            CREATE OR REPLACE FUNCTION public.erpclaw_decimal_sum_sfunc(numeric, text)
            RETURNS numeric LANGUAGE sql IMMUTABLE AS
            $$ SELECT $1 + COALESCE($2::numeric, 0) $$;
            """
        )
        cur.execute(
            """
            CREATE OR REPLACE FUNCTION public.erpclaw_decimal_sum_ffunc(numeric)
            RETURNS text LANGUAGE sql IMMUTABLE AS
            $$ SELECT $1::text $$;
            """
        )
        cur.execute(
            """
            CREATE AGGREGATE public.decimal_sum(text) (
              sfunc = public.erpclaw_decimal_sum_sfunc,
              stype = numeric,
              finalfunc = public.erpclaw_decimal_sum_ffunc,
              initcond = '0'
            );
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def _resolve_pg_url(db_path=None) -> str:
    """Resolve the PostgreSQL connection URL for the active config.

    Precedence: a postgres URL passed as ``db_path``, then ``ERPCLAW_DB_URL``,
    then a postgres ``ERPCLAW_DB_PATH``, then ``DB_HOST`` / ``DB_PORT`` /
    ``DB_NAME`` / ``DB_USER`` / ``DB_PASSWORD``. A SQLite file path is not
    treated as a URL. Raises if none of those is set.
    """
    for candidate in (
        db_path,
        os.environ.get("ERPCLAW_DB_URL"),
        os.environ.get("ERPCLAW_DB_PATH"),
    ):
        if _is_postgres_url(candidate):
            return str(candidate).strip()
    built = postgres_url_from_env()
    if built:
        return built
    raise RuntimeError(
        "ERPCLAW_DB_DIALECT=postgresql but no connection URL "
        "(set ERPCLAW_DB_URL, DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD, "
        "or pass db_path)."
    )


def _pg_connect_with_retry(psycopg2, url, *, cursor_factory, attempts=4):
    """Open a psycopg2 connection, retrying transient connection failures.

    A real Postgres deployment reaches the server over a network (or, in the
    test harness, an SSH tunnel). A momentary blip — the tunnel re-establishing,
    a transient DNS/TCP hiccup, the server reloading — surfaces as
    ``OperationalError`` at connect time. A single attempt turns that blip into
    a hard failure; a few bounded retries with backoff ride it out. SQLite is a
    local file and never takes this path.

    Only connection-level ``OperationalError`` is retried. Auth failures,
    missing-database, and every other error re-raise immediately so a real
    misconfiguration still fails fast. The final attempt's exception propagates
    unchanged so callers see the true cause, not a swallowed one.
    """
    delay = 0.5
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return psycopg2.connect(url, cursor_factory=cursor_factory)
        except psycopg2.OperationalError as exc:
            msg = str(exc).lower()
            # Retry only transient connection-establishment failures, not auth
            # ("password authentication failed") or missing-db ("does not exist").
            transient = (
                "could not connect" in msg
                or "connection refused" in msg
                or "connection reset" in msg
                or "timeout expired" in msg
                or "server closed the connection" in msg
                or "could not translate host" in msg
                or "no route to host" in msg
                or "temporarily unavailable" in msg
                or "the database system is starting up" in msg
            )
            if not transient or attempt == attempts:
                raise
            last_exc = exc
            time.sleep(delay)
            delay = min(delay * 2, 4.0)
    # Unreachable: the loop either returns or raises. Guard for clarity.
    raise last_exc  # pragma: no cover


def get_connection(db_path=None):
    """Get a database connection with ERPClaw standard settings, dialect-aware.

    SQLite (default) applies:
      - PRAGMA journal_mode=WAL  (concurrent reads during writes)
      - PRAGMA foreign_keys=ON   (enforce FK constraints)
      - PRAGMA busy_timeout=5000 (wait 5s on lock contention)
      - the ``decimal_sum`` aggregate, ``sqlite3.Row`` row access, and a
        0600 permission bit on freshly-created DB files.

    PostgreSQL (``ERPCLAW_DB_DIALECT=postgresql``) returns a
    :class:`PgConnectionWrapper` over a psycopg2 connection with a
    ``DictCursor`` and ``lock_timeout`` / ``statement_timeout`` set. The
    wrapper exposes the same ``conn.execute(sql, params)`` API, translating
    ``?`` placeholders to ``%s``.

    Args:
        db_path: SQLite file path, or a Postgres URL when the dialect is
                 postgresql. Defaults to ~/.openclaw/erpclaw/data.sqlite
                 (SQLite) or ERPCLAW_DB_URL / ERPCLAW_DB_PATH (Postgres).
                 Also checks the ERPCLAW_DB_PATH environment variable.

    Returns:
        ConnectionWrapper (SQLite) or PgConnectionWrapper (PostgreSQL).
    """
    if get_dialect() == "postgresql":
        import psycopg2
        from psycopg2.extras import DictCursor
        url = _resolve_pg_url(db_path)
        conn = _pg_connect_with_retry(psycopg2, url, cursor_factory=DictCursor)
        try:
            from erpclaw_lib.pg_ddl import install_read_adapters
            install_read_adapters(conn)
        except ImportError:
            pass
        setup_pragmas(conn)
        # Mirror the SQLite create_aggregate registration: ensure the
        # decimal_sum() SQL aggregate exists for exact financial sums.
        _ensure_pg_decimal_sum(conn)
        # SET lock_timeout/statement_timeout opened an implicit transaction;
        # commit so the connection is handed back idle, not in-transaction.
        conn.commit()
        return _bind_tenant_on_connect(PgConnectionWrapper(conn))

    raise RuntimeError(
        "ERPClaw requires DB_TYPE=postgres. SQLite is not a supported runtime "
        "(use migrate_sqlite_to_postgres.py for one-off imports)."
    )


def _bind_tenant_on_connect(wrapped):
    """Stamp ERPCLAW_TENANT_ID onto the connection (row isolation + RLS GUC)."""
    from erpclaw_lib.tenant import (
        apply_rls_session,
        bind_connection,
        current_tenant_id,
        current_tenant_ids,
        is_superadmin,
    )

    tid = current_tenant_id()
    tids = current_tenant_ids()
    if tid:
        bind_connection(wrapped, tid)
        apply_rls_session(wrapped, tid)
    elif tids:
        apply_rls_session(wrapped, tenant_ids=tids)
    elif is_superadmin():
        apply_rls_session(wrapped, bypass=True)
    else:
        # No env tenant — leave bypass so schema/bootstrap scripts can run;
        # hotel skill subprocesses always set ERPCLAW_TENANT_ID (strict).
        apply_rls_session(wrapped, bypass=True)
    return wrapped


def _reject_url_shaped_path(path: str) -> None:
    """Refuse a connection URL handed in where a filesystem path belongs.

    This is not hypothetical tidiness. `ensure_db_exists` used to run
    `os.makedirs` on whatever it was given, so a caller passing
    `postgresql://user:secret@host/db` created a DIRECTORY TREE NAMED AFTER THE
    CONNECTION STRING — credentials and all — and then SQLite happily made a
    database file inside it. Twice observed: a June-era tree on the test server
    whose directory name embedded the then-current PostgreSQL password, and a
    zero-byte `postgresql:/erpclaw@localhost/erpclaw_test` file that reached a
    commit on 2026-08-13.

    Writing a password into a filename is worse than the failure it replaces.
    A path that names a scheme is a caller mistake every time, so it fails
    loudly here rather than being silently turned into a directory.
    """
    if "://" in path or path.split(":", 1)[0] in _URL_SCHEMES:
        scheme = path.split(":", 1)[0]
        raise ValueError(
            "database path looks like a %s connection URL, not a filesystem "
            "path: pass it as ERPCLAW_DB_URL (or the module's --db-url) so it "
            "is parsed as a URL. Refusing rather than creating a directory "
            "named after it, because a URL can carry a password and a "
            "directory name is not a place for one." % scheme
        )


_URL_SCHEMES = frozenset({"postgresql", "postgres", "psql", "mysql", "sqlite"})


def ensure_db_exists(db_path=None) -> str:
    """Ensure the database directory exists.

    Creates parent directories if needed. Does not create the database.

    Refuses a value that names a URL scheme; see `_reject_url_shaped_path`.

    Args:
        db_path: Path to the database file.

    Returns:
        The resolved database path.
    """
    path = db_path or DEFAULT_DB_PATH
    _reject_url_shaped_path(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path
