"""PostgreSQL row-level security for ERP / hospitality (schema ``public``).

Session GUCs (set per connection from :class:`tenant.TenantContext`):

  * ``app.tenant_bypass`` = ``1`` → SuperAdmin, jobs, or no request context
  * ``app.tenant_id`` → single-hotel isolation (hotel staff / impersonation)
  * ``app.tenant_ids`` → comma-separated UUIDs for group ``ALL`` mode

Policies use ``tenant_id`` or ``company_id`` (or ``company.id``). WebClaw
platform tables in schema ``webclaw`` are not covered here.

SQL rewriting (:class:`db.TenantScopedConnection`) is skipped on Postgres
when RLS is installed; SQLite keeps the rewrite path.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tenant import TenantContext

log = logging.getLogger("webclaw.pg_rls")

_UUIDISH = re.compile(r"^[0-9a-fA-F,-]+$")
_POLICY = "webclaw_tenant_isolation"
_installed = False
_install_lock = threading.Lock()


def rls_enabled() -> bool:
    """True after :func:`ensure_public_rls` succeeded for this process."""
    return _installed


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _safe_guc_value(value: str) -> str:
    """Reject values that are not UUID / comma-list shaped; escape quotes."""
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    if not _UUIDISH.match(cleaned):
        raise ValueError(f"refusing non-UUID tenant GUC value: {cleaned[:40]!r}")
    return cleaned.replace("'", "''")


def _policy_predicate(column: str) -> str:
    """USING / WITH CHECK expression for a tenant column."""
    col = _quote_ident(column)
    return f"""(
  current_setting('app.tenant_bypass', true) = '1'
  OR (
    NULLIF(current_setting('app.tenant_id', true), '') IS NOT NULL
    AND {col} = NULLIF(current_setting('app.tenant_id', true), '')
  )
  OR (
    NULLIF(current_setting('app.tenant_ids', true), '') IS NOT NULL
    AND {col} = ANY(
      string_to_array(NULLIF(current_setting('app.tenant_ids', true), ''), ',')
    )
  )
)"""


def _discover_tenant_tables(conn) -> list[tuple[str, str]]:
    """Return ``(table_name, tenant_column)`` for public base tables."""
    rows = conn.execute(
        """
        SELECT c.table_name,
               CASE
                 WHEN bool_or(c.column_name = 'tenant_id') THEN 'tenant_id'
                 WHEN bool_or(c.column_name = 'company_id') THEN 'company_id'
                 WHEN c.table_name = 'company' AND bool_or(c.column_name = 'id')
                   THEN 'id'
                 ELSE NULL
               END AS tenant_col
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema
         AND t.table_name = c.table_name
        WHERE c.table_schema = 'public'
          AND t.table_type = 'BASE TABLE'
          AND c.table_name NOT LIKE 'pg_%'
        GROUP BY c.table_name
        HAVING CASE
                 WHEN bool_or(c.column_name = 'tenant_id') THEN 'tenant_id'
                 WHEN bool_or(c.column_name = 'company_id') THEN 'company_id'
                 WHEN c.table_name = 'company' AND bool_or(c.column_name = 'id')
                   THEN 'id'
                 ELSE NULL
               END IS NOT NULL
        ORDER BY c.table_name
        """
    ).fetchall()
    out: list[tuple[str, str]] = []
    for row in rows:
        table = row[0] if not hasattr(row, "keys") else row["table_name"]
        col = row[1] if not hasattr(row, "keys") else row["tenant_col"]
        if table and col:
            out.append((str(table), str(col)))
    return out


def ensure_public_rls(conn) -> int:
    """ENABLE + FORCE RLS and install isolation policies on public tables.

    Idempotent. Returns the number of tables that received a policy.
    """
    global _installed
    from db_access import get_dialect

    if get_dialect() != "postgresql":
        return 0

    with _install_lock:
        if _installed:
            return 0
        tables = _discover_tenant_tables(conn)
        applied = 0
        for table, column in tables:
            t = _quote_ident(table)
            pred = _policy_predicate(column)
            try:
                conn.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
                conn.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
                conn.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {t}")
                conn.execute(
                    f"""
                    CREATE POLICY {_POLICY} ON {t}
                      FOR ALL
                      USING {pred}
                      WITH CHECK {pred}
                    """
                )
                applied += 1
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                log.warning("RLS install skipped for %s: %s", table, exc)
                continue
        try:
            conn.commit()
        except Exception:
            pass
        _installed = True
        log.info("Postgres RLS installed on %s public tenant tables", applied)
        return applied


def apply_rls_gucs(
    conn,
    *,
    tenant_id: str | None = None,
    tenant_ids: list[str] | None = None,
    bypass: bool = False,
) -> None:
    """Set session GUCs used by :func:`ensure_public_rls` policies."""
    from db_access import get_dialect

    if get_dialect() != "postgresql":
        return

    # SET LOCAL would die with the transaction; we use session SET so it
    # lasts for the connection lifetime (one request / one skill open).
    if bypass or (not tenant_id and not tenant_ids):
        conn.execute("SET app.tenant_bypass = '1'")
        conn.execute("SET app.tenant_id = ''")
        conn.execute("SET app.tenant_ids = ''")
        return

    conn.execute("SET app.tenant_bypass = '0'")
    if tenant_id:
        safe = _safe_guc_value(tenant_id)
        conn.execute(f"SET app.tenant_id = '{safe}'")
        conn.execute("SET app.tenant_ids = ''")
        return

    cleaned = [_safe_guc_value(t) for t in (tenant_ids or []) if t]
    cleaned = [t for t in cleaned if t]
    if not cleaned:
        conn.execute("SET app.tenant_bypass = '1'")
        conn.execute("SET app.tenant_id = ''")
        conn.execute("SET app.tenant_ids = ''")
        return
    joined = ",".join(cleaned)
    conn.execute("SET app.tenant_id = ''")
    conn.execute(f"SET app.tenant_ids = '{joined}'")


def apply_context_rls(conn, ctx: TenantContext | None = None) -> None:
    """Map WebClaw :class:`TenantContext` onto Postgres RLS session GUCs."""
    from db_access import get_dialect

    if get_dialect() != "postgresql":
        return

    if ctx is None:
        try:
            from tenant import get_tenant_context

            ctx = get_tenant_context()
        except Exception:
            ctx = None

    if ctx is None:
        # Background jobs / schema probes — no request tenant.
        apply_rls_gucs(conn, bypass=True)
        return

    tid = ctx.isolation_id
    if tid:
        apply_rls_gucs(conn, tenant_id=tid)
        return

    if ctx.scope_all_properties and ctx.assigned_properties:
        apply_rls_gucs(conn, tenant_ids=list(ctx.assigned_properties))
        return

    # SuperAdmin (not impersonating) or empty isolation → bypass.
    apply_rls_gucs(conn, bypass=True)
