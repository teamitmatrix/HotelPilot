"""Row-level tenant context for ERPClaw / PMS.

Canonical tenant key
--------------------
``tenant_id`` IS ``company.id``. A hotel (PMS tenant) is one ERP company.
Hospitality tables store the same UUID as ``tenant_id``; ERP tables store it
as ``company_id``. Never invent a second UUID.

How context is established
--------------------------
1. WebClaw JWT middleware extracts ``tenant_id`` from the access token.
2. Skill execution injects ``--company-id`` / ``--tenant-id`` AND sets
   ``ERPCLAW_TENANT_ID`` in the subprocess environment.
3. This module is the single reader of that environment. Every skill that
   resolves a company should go through :func:`resolve_tenant_id` so a
   missing or mismatched tenant fails closed.

PostgreSQL migration path
-------------------------
Keep using this helper. On Postgres, set ``SET app.tenant_id = ...`` from
:func:`apply_rls_session` and add RLS policies of the form::

    USING (company_id = current_setting('app.tenant_id', true))

No call-site change is required: the same UUID still flows through
``ERPCLAW_TENANT_ID`` / ``conn.company_id``.
"""
from __future__ import annotations

import os

TENANT_ENV = "ERPCLAW_TENANT_ID"
TENANT_IDS_ENV = "ERPCLAW_TENANT_IDS"
GROUP_ENV = "ERPCLAW_GROUP_ID"
TENANT_STRICT_ENV = "ERPCLAW_TENANT_STRICT"
TENANT_ROLE_ENV = "ERPCLAW_TENANT_ROLE"  # "hotel" | "superadmin"

# Platform actions that may run without a hotel tenant (SuperAdmin / install).
PLATFORM_ACTIONS = frozenset({
    "initialize-database",
    "migrate",
    "setup-company",
    "list-companies",
    "check-installation",
    "install-guide",
    "status",
    "seed-defaults",
    "seed-permissions",
})


def current_tenant_id() -> str | None:
    """Return the tenant UUID from the process environment, or None."""
    value = (os.environ.get(TENANT_ENV) or "").strip()
    return value or None


def current_tenant_ids() -> list[str]:
    """Property UUIDs for group-wide aggregation (``ERPCLAW_TENANT_IDS``)."""
    raw = (os.environ.get(TENANT_IDS_ENV) or "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def current_group_id() -> str | None:
    """Parent group UUID when aggregating or scoping a branch."""
    value = (os.environ.get(GROUP_ENV) or "").strip()
    return value or None


def is_superadmin() -> bool:
    """True when the process was launched in platform-admin context."""
    return (os.environ.get(TENANT_ROLE_ENV) or "").strip().lower() == "superadmin"


def is_strict() -> bool:
    """Hotel requests must fail if tenant context is missing."""
    flag = (os.environ.get(TENANT_STRICT_ENV) or "").strip().lower()
    return flag in ("1", "true", "yes")


def bind_connection(conn, tenant_id: str):
    """Stamp tenant identity on the connection (naming series, audits)."""
    conn.company_id = tenant_id
    conn.tenant_id = tenant_id
    return conn


def apply_rls_session(
    conn,
    tenant_id: str | None = None,
    *,
    tenant_ids: list[str] | None = None,
    bypass: bool = False,
) -> None:
    """Set Postgres session GUCs used by RLS policies. No-op on SQLite.

    GUCs (installed by WebClaw ``pg_rls.ensure_public_rls``)::

        app.tenant_bypass = '1'   -- SuperAdmin / jobs / no isolation
        app.tenant_id             -- single hotel UUID
        app.tenant_ids            -- comma-separated UUIDs (group ALL)

    Policies match ``tenant_id`` / ``company_id`` / ``company.id`` against
    these settings.
    """
    from erpclaw_lib.db import get_dialect

    if get_dialect() != "postgresql":
        return

    def _safe(value: str) -> str:
        cleaned = (value or "").strip()
        for ch in cleaned:
            if ch not in "0123456789abcdefABCDEF,-":
                raise ValueError("refusing non-UUID tenant GUC value")
        return cleaned.replace("'", "''")

    tid = (tenant_id or "").strip() or None
    ids = [t for t in (tenant_ids or []) if t]
    if bypass:
        conn.execute("SET app.tenant_bypass = '1'")
        conn.execute("SET app.tenant_id = ''")
        conn.execute("SET app.tenant_ids = ''")
        return
    if tid:
        safe = _safe(tid)
        conn.execute("SET app.tenant_bypass = '0'")
        conn.execute(f"SET app.tenant_id = '{safe}'")
        conn.execute("SET app.tenant_ids = ''")
        return
    if ids:
        cleaned = [_safe(t) for t in ids]
        cleaned = [t for t in cleaned if t]
        if cleaned:
            conn.execute("SET app.tenant_bypass = '0'")
            conn.execute("SET app.tenant_id = ''")
            conn.execute(f"SET app.tenant_ids = '{','.join(cleaned)}'")
            return
    # Nothing to scope — bypass so install/bootstrap still works.
    conn.execute("SET app.tenant_bypass = '1'")
    conn.execute("SET app.tenant_id = ''")
    conn.execute("SET app.tenant_ids = ''")


def resolve_tenant_id(
    provided_company_id: str | None = None,
    provided_tenant_id: str | None = None,
    *,
    allow_missing: bool = False,
) -> str | None:
    """Reconcile payload tenant/company with the authenticated context.

    Rules:
      * Context tenant wins. A payload value that disagrees is a hard error.
      * If context is set and payload is empty, context is used.
      * If context is empty and strict mode is on, fail (unless allow_missing).
      * SuperAdmin with no payload may proceed (returns None) for platform ops.

    Returns the UUID to use, or None when SuperAdmin is acting without a hotel.
    """
    from erpclaw_lib.response import err

    ctx = current_tenant_id()
    payload = (provided_tenant_id or provided_company_id or "").strip() or None

    if ctx:
        if payload and payload != ctx:
            err(
                "tenant_id mismatch: the authenticated hotel does not match "
                "the tenant in the request payload",
                suggestion="Omit tenant_id/company_id; it is bound from your session.",
            )
        return ctx

    if payload:
        if is_strict() and not is_superadmin():
            err(
                "tenant_id is missing from the authenticated session",
                suggestion="Sign in again. Hotel staff cannot select another hotel.",
            )
        return payload

    # Group-wide reads (X-Property-ID=ALL) set ERPCLAW_TENANT_IDS without a
    # single ERPCLAW_TENANT_ID. ConnectionWrapper scopes SQL with IN (...).
    if current_tenant_ids():
        return None

    if allow_missing or is_superadmin():
        return None

    if is_strict():
        err(
            "tenant_id is required; refusing to run a hotel action without isolation",
            suggestion="Sign in as a hotel user, or pass --company-id as SuperAdmin.",
        )
    return None
