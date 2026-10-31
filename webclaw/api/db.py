"""Database connection helper for Webclaw.

Provides connections to:
1. Webclaw's own DB (auth, sessions, chat, config, audit)
2. Skill-specific DBs (for entity resolution, read from SKILL.md)

Replaces erpclaw_lib.db — webclaw has zero dependency on any skill's shared lib.
"""
import os
import re
import sys

import yaml

from skills.executor import find_skill_md, iter_skill_md_paths
from env_loader import load_api_env, resolve_erpclaw_db_path, resolve_webclaw_db_path

load_api_env()

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DEFAULT_WEBCLAW_DB = resolve_webclaw_db_path()

# Cache: skill_name → db_path (from SKILL.md webclaw.database)
_skill_db_cache: dict[str, str] = {}
_tables_initialized = False


class TenantScopedConnection:
    """Connection facade that enforces hotel row isolation.

    On PostgreSQL:
      1. Session GUCs + RLS policies (``pg_rls``) from TenantContext
      2. SQL rewrite kept as defense-in-depth (DB superusers bypass RLS)

    SuperAdmin (``isolation_id`` is None) bypasses RLS and is not rewritten.
    """

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_tenant_col_cache", {})
        object.__setattr__(self, "_rls_applied_for", object())  # sentinel ≠ any ctx
        try:
            from pg_rls import apply_context_rls, ensure_public_rls

            ensure_public_rls(conn)
            apply_context_rls(conn)
            from tenant import get_tenant_context

            object.__setattr__(self, "_rls_applied_for", get_tenant_context())
        except Exception as e:
            import sys

            print(f"WARNING: Postgres RLS init: {e}", file=sys.stderr)

    def _sync_rls(self) -> None:
        from pg_rls import apply_context_rls
        from tenant import get_tenant_context

        ctx = get_tenant_context()
        if ctx is object.__getattribute__(self, "_rls_applied_for"):
            return
        apply_context_rls(self._conn, ctx)
        object.__setattr__(self, "_rls_applied_for", ctx)

    def _scope_sql(self, sql, params):
        from sql_tenant import (
            apply_multi_property_predicate,
            apply_tenant_predicate,
            sqlite_has_group_column,
            sqlite_tenant_column,
        )
        from tenant import get_tenant_context

        ctx = get_tenant_context()
        if not ctx:
            return sql, params
        tid = ctx.isolation_id
        cache = self._tenant_col_cache

        def column_for(table):
            if table not in cache:
                cache[table] = sqlite_tenant_column(self._conn, table)
            return cache[table]

        if tid:
            return apply_tenant_predicate(sql, params, tid, column_for)

        if ctx.scope_all_properties:
            return apply_multi_property_predicate(
                sql,
                params,
                list(ctx.assigned_properties or ()),
                column_for,
                group_id=ctx.group_id,
                has_group_column=lambda t: sqlite_has_group_column(self._conn, t),
            )
        return sql, params

    def execute(self, sql: str, params=None):
        self._sync_rls()
        sql, params = self._scope_sql(sql, params)
        if params is None:
            return self._conn.execute(sql)
        return self._conn.execute(sql, params)

    def executemany(self, sql: str, seq_of_params):
        self._sync_rls()
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


def _is_postgres_url(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.strip().lower()
    return lowered.startswith("postgres://") or lowered.startswith("postgresql://")


def get_connection(db_path: str | None = None):
    """Get a connection to webclaw's own database (PostgreSQL, schema ``webclaw``).

    This database stores auth (users, sessions, roles), chat history,
    config, and audit logs — independent of any skill's database.

    Do **not** wrap with ``TenantScopedConnection``. On webclaw tables,
    ``tenant_id`` means home hotel / membership / role assignment — not
    "row belongs only to the active property". Auto-rewriting breaks
    ``/auth/me`` when an owner has switched to another branch.

    Args:
        db_path: Optional PostgreSQL URL override. File paths are ignored.

    Returns:
        A ``PostgresFacade``.
    """
    from db_access import connect
    from env_loader import require_platform_postgres

    global _tables_initialized

    require_platform_postgres()
    url = db_path if _is_postgres_url(db_path) else None
    conn = connect(url, search_path="webclaw")
    if not _tables_initialized:
        try:
            from init_webclaw_db import init_tables
            init_tables(conn)
            from property_scope import ensure_group_schema_webclaw
            ensure_group_schema_webclaw(conn)
            _tables_initialized = True
        except Exception as e:
            print(f"WARNING: webclaw schema init: {e}", file=sys.stderr)
    return conn


def get_skill_db(skill_name: str):
    """Get a DB connection for a skill's data (ERP / hospitality, schema ``public``).

    Always opens the shared Postgres URL. SKILL.md file paths are ignored.

    Args:
        skill_name: The skill directory name (e.g., 'erpclaw', 'hospitalityclaw').

    Returns:
        Tenant-scoped connection facade, or None if the skill name is invalid.
    """
    from db_access import connect
    from env_loader import require_platform_postgres

    if not _SKILL_NAME_RE.match(skill_name):
        return None

    require_platform_postgres()
    return TenantScopedConnection(connect(None, search_path="public"))


def _resolve_skill_db_path(skill_name: str) -> str | None:
    """Resolve a legacy SQLite file path for a skill (escape-hatch / migration).

    Not used for runtime when ``DB_TYPE=postgres``.
    """
    if not _SKILL_NAME_RE.match(skill_name):
        return None
    if skill_name in _skill_db_cache:
        return _skill_db_cache[skill_name]

    skill_md = find_skill_md(skill_name)
    db_path = None

    try:
        if skill_md:
            with open(skill_md, "r") as f:
                content = f.read()
            if content.startswith("---"):
                end = content.index("---", 3)
                frontmatter = yaml.safe_load(content[3:end])
                if frontmatter:
                    # Check webclaw.database first (ERPClaw modules)
                    webclaw = frontmatter.get("webclaw", {})
                    if isinstance(webclaw, dict) and webclaw.get("database"):
                        db_path = webclaw["database"]
                    elif frontmatter.get("database"):
                        db_path = frontmatter["database"]
    except Exception:
        pass

    db_path = _prefer_env_db(skill_name, db_path)
    _skill_db_cache[skill_name] = db_path
    return db_path


def _prefer_env_db(skill_name: str, md_path: str | None) -> str:
    """Use api/.env paths when SKILL.md still points at ~/.openclaw or is unset."""
    if skill_name == "webclaw":
        return resolve_webclaw_db_path()
    env_path = resolve_erpclaw_db_path()
    if not md_path:
        return env_path
    expanded = os.path.expanduser(os.path.expandvars(str(md_path)))
    if (
        not expanded
        or "$" in expanded
        or ".openclaw" in expanded
        or not os.path.exists(expanded)
    ):
        return env_path
    return expanded


def get_all_skill_db_paths() -> dict[str, str]:
    """Discover skill DB locations for tooling / diagnostics.

    Every skill shares the same Postgres URL (schema ``public``).
    """
    from env_loader import postgres_url_from_env

    url = (
        os.environ.get("ERPCLAW_DB_URL")
        or os.environ.get("WEBCLAW_DB_URL")
        or postgres_url_from_env()
        or "postgresql://(env)"
    )
    paths: dict[str, str] = {}
    for skill_md in iter_skill_md_paths():
        skill_name = os.path.basename(os.path.dirname(skill_md))
        if _SKILL_NAME_RE.match(skill_name):
            paths[skill_name] = url
    return paths
