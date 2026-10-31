"""Group → Property hierarchy (multi-branch tenancy).

Canonical mapping
-----------------
* ``property_id`` IS the existing isolation UUID (``company.id`` / ``tenant_id`` /
  ``pms_tenant.id``). No second UUID is inventing for operational rows.
* ``group_id`` is the parent hotel group / brand. Owners may switch
  ``active_property_id`` to a specific property UUID or ``ALL`` to aggregate
  reads across every property in the group they own.

Headers
-------
* ``X-Property-ID`` — preferred active branch (UUID or ``ALL``)
* ``X-Tenant-Id`` — legacy alias (same semantics as a specific property UUID)
"""
from __future__ import annotations

import os
import uuid
from typing import Iterable

from db_access import run_ddl, table_columns, DbConn

PROPERTY_HEADER = "x-property-id"
PROPERTY_ALL = "ALL"


def ensure_group_schema_erp(conn: DbConn) -> None:
    """Create pms_group and add group_id on company / pms_tenant (ERP DB)."""
    run_ddl(
        conn,
        """
        CREATE TABLE IF NOT EXISTS pms_group (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            admin_email     TEXT,
            status          TEXT NOT NULL DEFAULT 'active'
                            CHECK(status IN ('active','paused','cancelled')),
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_pms_group_status ON pms_group(status);
        """
    )
    for table in ("company", "pms_tenant"):
        cols = table_columns(conn, table)
        if not cols:
            continue
        if "group_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN group_id TEXT")
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_group ON {table}(group_id)"
            )
    conn.commit()


def ensure_group_schema_webclaw(conn: DbConn) -> None:
    """Add group_id on webclaw_user (web UI DB)."""
    cols = table_columns(conn, "webclaw_user")
    if not cols:
        return
    if "group_id" not in cols:
        conn.execute("ALTER TABLE webclaw_user ADD COLUMN group_id TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_webclaw_user_group ON webclaw_user(group_id)"
        )
    conn.commit()


def _row_val(row, key: str, idx: int = 0):
    if row is None:
        return None
    if hasattr(row, "keys"):
        return row[key]
    return row[idx]


def backfill_groups_for_existing_properties(erp_conn: DbConn) -> int:
    """Ensure every company/pms_tenant has a group (1:1 for legacy hotels).

    Returns number of groups created.
    """
    ensure_group_schema_erp(erp_conn)
    created = 0
    try:
        tenants = erp_conn.execute(
            """SELECT id, company_id, hotel_name, admin_email
               FROM pms_tenant WHERE group_id IS NULL OR group_id = ''"""
        ).fetchall()
    except Exception:
        try:
            erp_conn.rollback()
        except Exception:
            pass
        tenants = []

    for row in tenants:
        tid = _row_val(row, "id", 0)
        company_id = _row_val(row, "company_id", 1) or tid
        name = _row_val(row, "hotel_name", 2) or "Hotel Group"
        email = _row_val(row, "admin_email", 3)
        gid = str(uuid.uuid4())
        erp_conn.execute(
            """INSERT INTO pms_group (id, name, admin_email, status)
               VALUES (?, ?, ?, 'active')""",
            (gid, f"{name} Group", email),
        )
        erp_conn.execute(
            "UPDATE pms_tenant SET group_id = ? WHERE id = ?",
            (gid, tid),
        )
        try:
            erp_conn.execute(
                "UPDATE company SET group_id = ? WHERE id = ?",
                (gid, company_id),
            )
        except Exception:
            try:
                erp_conn.rollback()
            except Exception:
                pass
        created += 1

    # Companies without pms_tenant rows
    try:
        orphans = erp_conn.execute(
            """SELECT id, name FROM company
               WHERE (group_id IS NULL OR group_id = '')
                 AND id NOT IN (SELECT company_id FROM pms_tenant WHERE company_id IS NOT NULL)"""
        ).fetchall()
    except Exception:
        try:
            erp_conn.rollback()
        except Exception:
            pass
        orphans = []
    for row in orphans:
        cid = _row_val(row, "id", 0)
        name = _row_val(row, "name", 1) or "Company"
        gid = str(uuid.uuid4())
        erp_conn.execute(
            """INSERT INTO pms_group (id, name, status) VALUES (?, ?, 'active')""",
            (gid, f"{name} Group"),
        )
        erp_conn.execute("UPDATE company SET group_id = ? WHERE id = ?", (gid, cid))
        created += 1

    erp_conn.commit()
    return created


def sync_user_group_ids(web_conn: DbConn, erp_conn: DbConn) -> None:
    """Copy group_id from pms_tenant onto webclaw_user via home tenant_id."""
    ensure_group_schema_webclaw(web_conn)
    ensure_group_schema_erp(erp_conn)
    users = web_conn.execute(
        "SELECT id, tenant_id FROM webclaw_user WHERE tenant_id IS NOT NULL"
    ).fetchall()
    for row in users:
        uid = _row_val(row, "id", 0)
        tid = _row_val(row, "tenant_id", 1)
        if not tid:
            continue
        g = erp_conn.execute(
            "SELECT group_id FROM pms_tenant WHERE id = ? OR company_id = ?",
            (tid, tid),
        ).fetchone()
        gid = _row_val(g, "group_id", 0) if g else None
        if gid:
            web_conn.execute(
                "UPDATE webclaw_user SET group_id = ? WHERE id = ?",
                (gid, uid),
            )
    web_conn.commit()


def create_group(
    erp_conn: DbConn,
    *,
    name: str,
    admin_email: str | None = None,
    group_id: str | None = None,
) -> str:
    ensure_group_schema_erp(erp_conn)
    gid = group_id or str(uuid.uuid4())
    erp_conn.execute(
        """INSERT INTO pms_group (id, name, admin_email, status)
           VALUES (?, ?, ?, 'active')""",
        (gid, name, admin_email),
    )
    erp_conn.commit()
    return gid


def _raw_conn(conn: DbConn) -> DbConn:
    """Unwrap TenantScopedConnection so cross-property admin UPDATEs are not rewritten."""
    return getattr(conn, "_conn", conn)


def attach_property_to_group(
    erp_conn: DbConn,
    property_id: str,
    group_id: str,
) -> None:
    ensure_group_schema_erp(erp_conn)
    raw = _raw_conn(erp_conn)
    raw.execute(
        "UPDATE company SET group_id = ? WHERE id = ?",
        (group_id, property_id),
    )
    raw.execute(
        "UPDATE pms_tenant SET group_id = ? WHERE id = ? OR company_id = ?",
        (group_id, property_id, property_id),
    )
    raw.commit()


def group_id_for_property(erp_conn: DbConn, property_id: str) -> str | None:
    ensure_group_schema_erp(erp_conn)
    row = erp_conn.execute(
        "SELECT group_id FROM pms_tenant WHERE id = ? OR company_id = ?",
        (property_id, property_id),
    ).fetchone()
    if row and _row_val(row, "group_id", 0):
        return str(_row_val(row, "group_id", 0))
    row = erp_conn.execute(
        "SELECT group_id FROM company WHERE id = ?", (property_id,)
    ).fetchone()
    if row and _row_val(row, "group_id", 0):
        return str(_row_val(row, "group_id", 0))
    return None


def _table_columns(conn: DbConn, table: str) -> set[str]:
    return table_columns(conn, table)


def _has_table(conn: DbConn, table: str) -> bool:
    from db_access import table_exists

    return table_exists(conn, table)


def ensure_accepting_bookings_column(conn: DbConn) -> None:
    """Owner can close a branch for new bookings without suspending staff access."""
    cols = table_columns(conn, "pms_tenant")
    if not cols:
        return
    if "accepting_bookings" not in cols:
        conn.execute(
            "ALTER TABLE pms_tenant ADD COLUMN accepting_bookings INTEGER NOT NULL DEFAULT 1"
        )
        conn.commit()


def is_accepting_bookings(erp_conn: DbConn, property_id: str) -> bool:
    """True when the property may take new website / OTA / front-desk bookings."""
    ensure_accepting_bookings_column(erp_conn)
    raw = _raw_conn(erp_conn)
    row = raw.execute(
        """SELECT status, accepting_bookings FROM pms_tenant
           WHERE id = ? OR company_id = ? LIMIT 1""",
        (property_id, property_id),
    ).fetchone()
    if not row:
        return True
    status = str(_row_val(row, "status", 0) or "active").lower()
    if status in ("suspended", "cancelled", "deleted", "closed"):
        return False
    try:
        accepting = _row_val(row, "accepting_bookings", 1)
    except Exception:
        accepting = 1
    if accepting is None:
        return True
    return int(accepting) == 1


def set_accepting_bookings(
    erp_conn: DbConn, property_id: str, accepting: bool
) -> dict:
    """Open or close a branch for new bookings. Staff can still operate the property."""
    ensure_accepting_bookings_column(erp_conn)
    raw = _raw_conn(erp_conn)
    row = raw.execute(
        """SELECT id, status, COALESCE(accepting_bookings, 1) AS accepting_bookings
           FROM pms_tenant WHERE id = ? OR company_id = ? LIMIT 1""",
        (property_id, property_id),
    ).fetchone()
    if not row:
        raise ValueError("Property not found")
    status = str(_row_val(row, "status", 1) or "active").lower()
    if status in ("cancelled", "deleted"):
        raise ValueError("Cancelled properties cannot be reopened for booking")
    if status == "suspended":
        raise ValueError("Suspended properties cannot accept bookings until resumed by platform admin")
    flag = 1 if accepting else 0
    tid = _row_val(row, "id", 0)
    raw.execute(
        """UPDATE pms_tenant
           SET accepting_bookings = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (flag, tid),
    )
    erp_conn.commit()
    return {
        "property_id": tid,
        "accepting_bookings": bool(flag),
        "status": "active" if flag else "closed",
        "tenant_status": status,
    }


def display_property_status(status: str | None, accepting_bookings: int | None) -> str:
    base = str(status or "active").lower()
    if base in ("suspended", "cancelled", "deleted"):
        return base
    if accepting_bookings is not None and int(accepting_bookings) == 0:
        return "closed"
    return "active" if base == "active" else base


def list_properties_in_group(erp_conn: DbConn, group_id: str) -> list[dict]:
    ensure_group_schema_erp(erp_conn)
    ensure_accepting_bookings_column(erp_conn)
    # Cross-property listing must not be tenant-rewritten to the active branch only.
    erp_conn = _raw_conn(erp_conn)
    # Property profile address lives on hospitalityclaw_hotel (settings → update-hotel).
    # company.address is not part of the ERP schema; keep a fallback if present.
    hotel_cols = (
        _table_columns(erp_conn, "hospitalityclaw_hotel")
        if _has_table(erp_conn, "hospitalityclaw_hotel")
        else set()
    )
    company_cols = _table_columns(erp_conn, "company") if _has_table(erp_conn, "company") else set()

    if "address" in hotel_cols:
        address_select = """(
            SELECT h.address FROM hospitalityclaw_hotel h
            WHERE h.tenant_id = COALESCE(t.company_id, t.id)
            ORDER BY h.created_at
            LIMIT 1
        ) AS address"""
        join_company = ""
    elif "address" in company_cols:
        address_select = "c.address AS address"
        join_company = "LEFT JOIN company c ON c.id = t.company_id"
    else:
        address_select = "NULL AS address"
        join_company = ""

    rows = erp_conn.execute(
        f"""SELECT t.id AS property_id, t.company_id, t.hotel_name, t.timezone,
                  t.country, t.currency, t.status, t.admin_email, t.group_id,
                  COALESCE(t.accepting_bookings, 1) AS accepting_bookings,
                  {address_select}
           FROM pms_tenant t
           {join_company}
           WHERE t.group_id = ?
             AND COALESCE(t.status, 'active') NOT IN ('cancelled', 'deleted')
           ORDER BY LOWER(t.hotel_name)""",
        (group_id,),
    ).fetchall()
    out = []
    for r in rows:
        if hasattr(r, "keys"):
            item = {k: r[k] for k in r.keys()}
        else:
            item = {
                "property_id": r[0],
                "company_id": r[1],
                "hotel_name": r[2],
                "timezone": r[3],
                "country": r[4],
                "currency": r[5],
                "status": r[6],
                "admin_email": r[7],
                "group_id": r[8],
                "accepting_bookings": r[9] if len(r) > 9 else 1,
                "address": r[10] if len(r) > 10 else None,
            }
        accepting = item.get("accepting_bookings")
        if accepting is None:
            accepting = 1
        item["accepting_bookings"] = bool(int(accepting))
        item["tenant_status"] = item.get("status") or "active"
        item["status"] = display_property_status(item.get("tenant_status"), int(accepting))
        out.append(item)
    return out


def property_ids_in_group(erp_conn: DbConn, group_id: str) -> list[str]:
    return [str(p["property_id"]) for p in list_properties_in_group(erp_conn, group_id)]


def user_group_id(web_conn: DbConn, user_id: str) -> str | None:
    ensure_group_schema_webclaw(web_conn)
    try:
        row = web_conn.execute(
            "SELECT group_id FROM webclaw_user WHERE id = ?", (user_id,)
        ).fetchone()
    except Exception:
        try:
            web_conn.rollback()
        except Exception:
            pass
        return None
    return (_row_val(row, "group_id", 0) or None) if row else None


def ensure_owner_group_for_user(
    web_conn: DbConn,
    user_id: str,
    tenant_id: str | None,
) -> str | None:
    """Ensure schema + group exist for a hotel user's home property.

    Returns the group_id (created/backfilled as needed) or None.
    """
    if not tenant_id:
        return None
    ensure_group_schema_webclaw(web_conn)
    gid = user_group_id(web_conn, user_id)
    if gid:
        return gid

    import db as dbmod

    erp = dbmod.get_skill_db("erpclaw")
    if not erp:
        return None
    try:
        ensure_group_schema_erp(erp)
        backfill_groups_for_existing_properties(erp)
        sync_user_group_ids(web_conn, erp)
        gid = user_group_id(web_conn, user_id)
        if gid:
            return gid
        # Explicit attach for this home property if still missing.
        gid = group_id_for_property(erp, tenant_id)
        if not gid:
            row = erp.execute(
                "SELECT hotel_name, admin_email FROM pms_tenant WHERE id = ? OR company_id = ?",
                (tenant_id, tenant_id),
            ).fetchone()
            name = (_row_val(row, "hotel_name", 0) if row else None) or "Hotel Group"
            email = _row_val(row, "admin_email", 1) if row else None
            gid = create_group(erp, name=f"{name} Group", admin_email=email)
            attach_property_to_group(erp, tenant_id, gid)
        else:
            attach_property_to_group(erp, tenant_id, gid)
        web_conn.execute(
            "UPDATE webclaw_user SET group_id = ? WHERE id = ?",
            (gid, user_id),
        )
        web_conn.commit()
        return gid
    finally:
        try:
            erp.close()
        except Exception:
            pass


def is_group_owner(roles: Iterable[str]) -> bool:
    """True when the caller may use X-Property-ID=ALL (group-wide roles)."""
    names = {str(r).strip() for r in roles}
    return bool(
        names
        & {
            "Hotel Owner",
            "HOTEL_OWNER",
            "Hotel Admin",
            "HOTEL_ADMIN",
            "General Manager",
            "GENERAL_MANAGER",
            "Hotel Accountant",
            "HOTEL_ACCOUNTANT",
        }
    )


def resolve_active_property_header(
    *,
    property_header: str | None,
    tenant_header: str | None,
) -> str | None:
    """Prefer X-Property-ID; fall back to legacy X-Tenant-Id."""
    raw = (property_header or "").strip()
    if raw:
        return raw
    legacy = (tenant_header or "").strip()
    return legacy or None
