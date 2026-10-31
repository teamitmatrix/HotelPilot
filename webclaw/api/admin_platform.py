"""SuperAdmin platform API — hotel tenants, subscription plans, analytics.

All endpoints require ``platform.admin`` (SUPER_ADMIN / System Manager).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

import db
from db_access import table_columns, table_exists, DbConn
from auth.jwt_utils import create_access_token, get_signing_secret
from models import (
    BillingOverrideRequest,
    CreateHotelTenantRequest,
    SubscriptionPlanRequest,
    UpdateHotelTenantRequest,
)
from auth.deps import get_optional_tenant
from rbac import require_any_permission, require_permission

router = APIRouter(prefix="/api/v1/admin", tags=["admin-platform"])

# pms_tenant CHECK still uses 'professional'; UI/API use 'business'.
PLAN_TO_TIER = {"starter": "starter", "business": "professional", "enterprise": "enterprise"}
TIER_TO_PLAN = {
    "starter": "starter",
    "professional": "business",
    "business": "business",
    "enterprise": "enterprise",
}

FEATURE_CATALOG = [
    {"key": "front_desk", "label": "Front desk & reservations", "description": "Bookings, check-in/out, guests"},
    {"key": "housekeeping", "label": "Housekeeping", "description": "Room status and cleaning boards"},
    {"key": "accounting", "label": "Accounting", "description": "Folios, invoices, and general ledger"},
    {"key": "ai_chat", "label": "AI chat", "description": "In-app assistant for hotel staff"},
    {"key": "reports", "label": "Advanced Analytics", "description": "Occupancy, ADR, revenue, and platform reports"},
    {"key": "multi_property", "label": "Multi-property", "description": "Manage more than one hotel under one owner"},
    {"key": "channel_manager", "label": "OTA Sync", "description": "OTA / GDS channel distribution"},
    {"key": "pos", "label": "POS / F&B", "description": "Restaurant and outlet charges on folios"},
    {"key": "api_access", "label": "API access", "description": "External integrations and webhooks"},
    {"key": "custom_branding", "label": "Custom branding", "description": "White-label guest-facing screens"},
]

_DEFAULT_FEATURES = {
    "starter": {
        "front_desk": True,
        "housekeeping": True,
        "accounting": False,
        "ai_chat": True,
        "reports": False,
        "multi_property": False,
        "channel_manager": False,
        "pos": False,
        "api_access": False,
        "custom_branding": False,
    },
    "business": {
        "front_desk": True,
        "housekeeping": True,
        "accounting": True,
        "ai_chat": True,
        "reports": True,
        "multi_property": False,
        "channel_manager": True,
        "pos": True,
        "api_access": False,
        "custom_branding": False,
    },
    "enterprise": {
        "front_desk": True,
        "housekeeping": True,
        "accounting": True,
        "ai_chat": True,
        "reports": True,
        "multi_property": True,
        "channel_manager": True,
        "pos": True,
        "api_access": True,
        "custom_branding": True,
    },
}

DEFAULT_PLANS = (
    {
        "code": "starter",
        "name": "Starter",
        "billing_cycle": "monthly",
        "monthly_price": "49",
        "annual_price": "490",
        "max_rooms": 25,
        "max_staff": 5,
        "sort_order": 1,
    },
    {
        "code": "business",
        "name": "Professional",
        "billing_cycle": "monthly",
        "monthly_price": "149",
        "annual_price": "1490",
        "max_rooms": 120,
        "max_staff": 25,
        "sort_order": 2,
    },
    {
        "code": "enterprise",
        "name": "Enterprise",
        "billing_cycle": "annual",
        "monthly_price": "399",
        "annual_price": "3990",
        "max_rooms": 0,
        "max_staff": 0,
        "sort_order": 3,
    },
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn, name: str) -> bool:
    """Dialect-aware table probe via ``db_access.table_exists``."""
    try:
        return table_exists(conn, name)
    except Exception:
        return False


def _erp() -> DbConn | None:
    return db.get_skill_db("erpclaw")


def _row_value(row, key: str, index: int):
    if row is None:
        return None
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return row[index]


def _parse_features(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _plan_to_dict(row) -> dict:
    code = _row_value(row, "code", 1)
    max_rooms = int(_row_value(row, "max_rooms", 6) or 0)
    # max_staff may be missing on older rows / schemas
    max_staff = 0
    try:
        if hasattr(row, "keys") and "max_staff" in row.keys():
            max_staff = int(row["max_staff"] or 0)
        else:
            max_staff = int(_row_value(row, "max_staff", 12) or 0)
    except Exception:
        defaults = {"starter": 5, "business": 25, "enterprise": 0}
        max_staff = defaults.get(str(code), 5)
    out = {
        "id": _row_value(row, "id", 0),
        "code": code,
        "name": _row_value(row, "name", 2),
        "billing_cycle": _row_value(row, "billing_cycle", 3),
        "monthly_price": str(_row_value(row, "monthly_price", 4) or "0"),
        "annual_price": str(_row_value(row, "annual_price", 5) or "0"),
        "max_rooms": max_rooms,
        "unlimited_rooms": max_rooms <= 0,
        "max_staff": max_staff,
        "unlimited_staff": max_staff <= 0,
        "features": _parse_features(_row_value(row, "features", 7)),
        "is_active": bool(_row_value(row, "is_active", 8)),
        "sort_order": int(_row_value(row, "sort_order", 9) or 0),
        "created_at": _row_value(row, "created_at", 10),
        "updated_at": _row_value(row, "updated_at", 11),
    }
    if hasattr(row, "keys"):
        for key in ("stripe_product_id", "stripe_price_monthly_id", "stripe_price_annual_id"):
            if key in row.keys():
                out[key] = row[key]
    return out


def ensure_subscription_plans(conn: DbConn) -> None:
    """Create the plans table and seed Starter / Professional / Enterprise."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS webclaw_subscription_plan (
            id              TEXT PRIMARY KEY,
            code            TEXT NOT NULL UNIQUE,
            name            TEXT NOT NULL,
            billing_cycle   TEXT NOT NULL DEFAULT 'monthly'
                            CHECK(billing_cycle IN ('monthly','annual')),
            monthly_price   TEXT NOT NULL DEFAULT '0',
            annual_price    TEXT NOT NULL DEFAULT '0',
            max_rooms       INTEGER NOT NULL DEFAULT 20,
            features        TEXT NOT NULL DEFAULT '{}',
            is_active       INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
            sort_order      INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        )"""
    )
    cols = table_columns(conn, "webclaw_subscription_plan") or set()
    if "max_staff" not in cols:
        try:
            conn.execute(
                "ALTER TABLE webclaw_subscription_plan ADD COLUMN max_staff INTEGER NOT NULL DEFAULT 5"
            )
        except Exception:
            pass
    try:
        from stripe_billing import ensure_stripe_tables
        ensure_stripe_tables(conn)
    except Exception:
        pass
    count = conn.execute("SELECT COUNT(*) FROM webclaw_subscription_plan").fetchone()[0]
    if count:
        # Align legacy Business label → Professional and backfill max_staff
        conn.execute(
            "UPDATE webclaw_subscription_plan SET name = 'Professional' WHERE code = 'business' AND name = 'Business'"
        )
        for spec in DEFAULT_PLANS:
            conn.execute(
                "UPDATE webclaw_subscription_plan SET max_staff = ? WHERE code = ?",
                (spec["max_staff"], spec["code"]),
            )
        conn.commit()
        return
    now = _now()
    for spec in DEFAULT_PLANS:
        conn.execute(
            """INSERT INTO webclaw_subscription_plan
               (id, code, name, billing_cycle, monthly_price, annual_price,
                max_rooms, max_staff, features, is_active, sort_order, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                spec["code"],
                spec["name"],
                spec["billing_cycle"],
                spec["monthly_price"],
                spec["annual_price"],
                spec["max_rooms"],
                spec["max_staff"],
                json.dumps(_DEFAULT_FEATURES[spec["code"]]),
                spec["sort_order"],
                now,
                now,
            ),
        )
    conn.commit()


BILLING_DDL = """
CREATE TABLE IF NOT EXISTS webclaw_tenant_billing_override (
    tenant_id          TEXT PRIMARY KEY,
    trial_ends_at      TEXT,
    discount_percent   REAL NOT NULL DEFAULT 0,
    discount_note      TEXT,
    updated_at         TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS webclaw_subscription_event (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    detail          TEXT,
    actor_email     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sub_event_tenant
    ON webclaw_subscription_event(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS webclaw_tenant_lifecycle (
    tenant_id       TEXT PRIMARY KEY,
    phase           TEXT NOT NULL DEFAULT 'active'
                    CHECK(phase IN (
                        'trial','onboarding','active','past_due','suspended','cancelled'
                    )),
    cancelled_at    TEXT,
    purge_after     TEXT,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tenant_lifecycle_phase
    ON webclaw_tenant_lifecycle(phase, purge_after);
"""


def ensure_billing_tables(conn: DbConn) -> None:
    """Tenant billing overrides + subscription event history + Stripe linkage."""
    from db_access import run_ddl

    run_ddl(conn, BILLING_DDL)
    try:
        from stripe_billing import ensure_stripe_tables
        ensure_stripe_tables(conn)
    except Exception:
        conn.commit()


def _upsert_lifecycle(
    conn: DbConn,
    tenant_id: str,
    phase: str,
    *,
    cancelled_at: str | None = None,
    purge_after: str | None = None,
) -> None:
    ensure_billing_tables(conn)
    now = _now()
    conn.execute(
        """INSERT INTO webclaw_tenant_lifecycle
           (tenant_id, phase, cancelled_at, purge_after, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(tenant_id) DO UPDATE SET
             phase = excluded.phase,
             cancelled_at = COALESCE(excluded.cancelled_at, webclaw_tenant_lifecycle.cancelled_at),
             purge_after = COALESCE(excluded.purge_after, webclaw_tenant_lifecycle.purge_after),
             updated_at = excluded.updated_at""",
        (tenant_id, phase, cancelled_at, purge_after, now),
    )
    conn.commit()


def _get_lifecycle(conn: DbConn, tenant_id: str) -> dict | None:
    ensure_billing_tables(conn)
    row = conn.execute(
        "SELECT phase, cancelled_at, purge_after, updated_at FROM webclaw_tenant_lifecycle WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "phase": _row_value(row, "phase", 0),
        "cancelled_at": _row_value(row, "cancelled_at", 1),
        "purge_after": _row_value(row, "purge_after", 2),
        "updated_at": _row_value(row, "updated_at", 3),
    }


def resolve_lifecycle_phase(
    *,
    erp_status: str | None,
    billing: dict | None = None,
    stripe_status: str | None = None,
    onboarding_status: str | None = None,
    lifecycle_row: dict | None = None,
) -> str:
    """Compute operator-facing lifecycle phase without widening ERP CHECK."""
    if lifecycle_row and lifecycle_row.get("phase") == "cancelled":
        return "cancelled"
    status = (erp_status or "active").lower()
    if status == "cancelled":
        return "cancelled"
    if status == "suspended":
        return "suspended"
    stripe = (stripe_status or "").lower()
    if stripe in ("past_due", "unpaid", "incomplete"):
        return "past_due"
    if onboarding_status and onboarding_status not in ("live", "missing", None):
        return "onboarding"
    trial_ends = (billing or {}).get("trial_ends_at")
    if trial_ends:
        try:
            t = datetime.fromisoformat(str(trial_ends).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t > datetime.now(timezone.utc):
                return "trial"
        except Exception:
            pass
    if lifecycle_row and lifecycle_row.get("phase") in (
        "trial", "onboarding", "past_due", "active"
    ):
        return str(lifecycle_row["phase"])
    return "active"


def soft_cancel_tenant(tenant_id: str, *, actor_email: str | None = None) -> dict:
    """Mark hotel cancelled, disable staff, retain ERP row for retention/purge."""
    web_conn = db.get_connection()
    ensure_billing_tables(web_conn)
    erp = _erp()
    if not erp:
        return {"ok": False, "error": "Hotel database is not available", "http_status": 503}
    try:
        row = _get_tenant_row(erp, tenant_id)
        if not row:
            return {"ok": False, "error": "Hotel tenant not found", "http_status": 404}
        tid = str(_row_value(row, "id", 0))
        erp.execute(
            "UPDATE pms_tenant SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (tid,),
        )
        erp.commit()
        _set_hotel_users_status(tid, "disabled")
        retention_days = 365
        try:
            from admin_growth import ensure_growth_tables

            ensure_growth_tables(web_conn)
            pol = web_conn.execute(
                "SELECT retention_days FROM webclaw_compliance_policy WHERE id = 'default'"
            ).fetchone()
            if pol:
                retention_days = int(_row_value(pol, "retention_days", 0) or 365)
        except Exception:
            retention_days = 365
        cancelled_at = _now()
        try:
            purge_dt = datetime.fromisoformat(cancelled_at.replace("Z", "+00:00")) + timedelta(
                days=retention_days
            )
            purge_after = purge_dt.isoformat()
        except Exception:
            purge_after = cancelled_at
        _upsert_lifecycle(
            web_conn,
            tid,
            "cancelled",
            cancelled_at=cancelled_at,
            purge_after=purge_after,
        )
        _record_subscription_event(
            web_conn,
            tid,
            "hotel_cancelled",
            f"Soft-cancelled; purge after {purge_after}",
            actor_email,
        )
        return {
            "ok": True,
            "tenant_id": tid,
            "tenant_status": "cancelled",
            "lifecycle": "cancelled",
            "purge_after": purge_after,
        }
    finally:
        erp.close()


def purge_tenant_hard(tenant_id: str, *, actor_email: str | None = None) -> dict:
    """Hard-delete a cancelled hotel registry row (after retention)."""
    web_conn = db.get_connection()
    ensure_billing_tables(web_conn)
    life = _get_lifecycle(web_conn, tenant_id)
    erp = _erp()
    if not erp:
        return {"ok": False, "error": "Hotel database is not available", "http_status": 503}
    try:
        row = _get_tenant_row(erp, tenant_id)
        if not row:
            # Already gone — clean lifecycle
            web_conn.execute(
                "DELETE FROM webclaw_tenant_lifecycle WHERE tenant_id = ?", (tenant_id,)
            )
            web_conn.commit()
            return {"ok": True, "tenant_id": tenant_id, "message": "Already purged"}
        status = str(_row_value(row, "status", 7) or "")
        if status != "cancelled" and (not life or life.get("phase") != "cancelled"):
            return {
                "ok": False,
                "error": "Hotel must be cancelled before purge",
                "http_status": 400,
            }
        tid = str(_row_value(row, "id", 0))
        erp.execute("DELETE FROM pms_tenant WHERE id = ?", (tid,))
        erp.commit()
        _set_hotel_users_status(tid, "disabled")
        web_conn.execute("DELETE FROM webclaw_tenant_lifecycle WHERE tenant_id = ?", (tid,))
        web_conn.commit()
        _record_subscription_event(
            web_conn, tid, "hotel_purged", "Hard-deleted tenant registry", actor_email
        )
        return {"ok": True, "tenant_id": tid, "message": "Hotel purged"}
    finally:
        erp.close()


def run_due_tenant_purges() -> int:
    """Purge cancelled tenants past retention when auto_purge is enabled."""
    web_conn = db.get_connection()
    ensure_billing_tables(web_conn)
    try:
        from admin_growth import ensure_growth_tables

        ensure_growth_tables(web_conn)
        pol = web_conn.execute(
            "SELECT auto_purge_cancelled FROM webclaw_compliance_policy WHERE id = 'default'"
        ).fetchone()
        if not pol or not bool(_row_value(pol, "auto_purge_cancelled", 0)):
            return 0
    except Exception:
        return 0
    now = _now()
    rows = web_conn.execute(
        """SELECT tenant_id FROM webclaw_tenant_lifecycle
           WHERE phase = 'cancelled'
             AND purge_after IS NOT NULL
             AND datetime(purge_after) <= datetime(?)""",
        (now,),
    ).fetchall()
    purged = 0
    for r in rows:
        tid = str(_row_value(r, "tenant_id", 0))
        result = purge_tenant_hard(tid, actor_email="retention_job")
        if result.get("ok"):
            purged += 1
    return purged


def _record_subscription_event(
    conn: DbConn,
    tenant_id: str,
    event_type: str,
    detail: str = "",
    actor_email: str | None = None,
) -> None:
    ensure_billing_tables(conn)
    conn.execute(
        """INSERT INTO webclaw_subscription_event
           (id, tenant_id, event_type, detail, actor_email, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), tenant_id, event_type, detail, actor_email, _now()),
    )
    conn.commit()


def _get_billing_override(conn: DbConn, tenant_id: str) -> dict:
    ensure_billing_tables(conn)
    row = conn.execute(
        "SELECT * FROM webclaw_tenant_billing_override WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchone()
    if not row:
        return {
            "trial_ends_at": None,
            "discount_percent": 0,
            "discount_note": None,
        }
    return {
        "trial_ends_at": _row_value(row, "trial_ends_at", 1),
        "discount_percent": float(_row_value(row, "discount_percent", 2) or 0),
        "discount_note": _row_value(row, "discount_note", 3),
    }


def _get_plan(conn, code: str):
    ensure_subscription_plans(conn)
    return conn.execute(
        "SELECT * FROM webclaw_subscription_plan WHERE code = ?",
        (code,),
    ).fetchone()


def _serialize_tenant(
    row,
    plan_by_code: dict[str, dict] | None = None,
    billing: dict | None = None,
    *,
    stripe_status: str | None = None,
    onboarding_status: str | None = None,
    lifecycle_row: dict | None = None,
) -> dict:
    tier = _row_value(row, "subscription_tier", 6) or "starter"
    plan_code = TIER_TO_PLAN.get(str(tier).lower(), "starter")
    plan = (plan_by_code or {}).get(plan_code)
    room_count = 0
    try:
        if hasattr(row, "keys") and "room_count" in row.keys():
            room_count = int(row["room_count"] or 0)
    except Exception:
        room_count = 0
    billing = billing or {}
    erp_status = _row_value(row, "status", 7)
    lifecycle = resolve_lifecycle_phase(
        erp_status=str(erp_status or "active"),
        billing=billing,
        stripe_status=stripe_status,
        onboarding_status=onboarding_status,
        lifecycle_row=lifecycle_row,
    )
    return {
        "id": _row_value(row, "id", 0),
        "hotel_name": _row_value(row, "hotel_name", 1),
        "admin_email": _row_value(row, "admin_email", 2),
        "timezone": _row_value(row, "timezone", 3),
        "country": _row_value(row, "country", 4),
        "currency": _row_value(row, "currency", 5),
        "subscription_tier": tier,
        "plan_code": plan_code,
        "plan_name": (plan or {}).get("name") or plan_code.replace("_", " ").title(),
        "max_rooms": (plan or {}).get("max_rooms"),
        "max_staff": (plan or {}).get("max_staff"),
        "status": erp_status,
        "lifecycle": lifecycle,
        "created_at": _row_value(row, "created_at", 8),
        "company_name": _row_value(row, "company_name", 9),
        "room_count": room_count,
        "trial_ends_at": billing.get("trial_ends_at"),
        "discount_percent": billing.get("discount_percent", 0),
        "discount_note": billing.get("discount_note"),
        "cancelled_at": (lifecycle_row or {}).get("cancelled_at"),
        "purge_after": (lifecycle_row or {}).get("purge_after"),
    }


def _load_plan_map(web_conn) -> dict[str, dict]:
    ensure_subscription_plans(web_conn)
    rows = web_conn.execute(
        "SELECT * FROM webclaw_subscription_plan ORDER BY sort_order, name"
    ).fetchall()
    return {_row_value(r, "code", 1): _plan_to_dict(r) for r in rows}


def _serialize_tenants_with_billing(erp, web_conn) -> list[dict]:
    plans = _load_plan_map(web_conn)
    ensure_billing_tables(web_conn)
    stripe_by_tenant: dict[str, str] = {}
    try:
        from stripe_billing import ensure_stripe_tables

        ensure_stripe_tables(web_conn)
        for sr in web_conn.execute(
            "SELECT tenant_id, status FROM webclaw_stripe_customer"
        ).fetchall():
            stripe_by_tenant[str(_row_value(sr, "tenant_id", 0))] = str(
                _row_value(sr, "status", 1) or ""
            )
    except Exception:
        pass
    onboard_by_tenant: dict[str, str] = {}
    try:
        from admin_onboarding import ensure_onboarding_tables

        ensure_onboarding_tables(web_conn)
        for orow in web_conn.execute(
            "SELECT tenant_id, status FROM webclaw_tenant_onboarding"
        ).fetchall():
            onboard_by_tenant[str(_row_value(orow, "tenant_id", 0))] = str(
                _row_value(orow, "status", 1) or ""
            )
    except Exception:
        pass
    out = []
    for r in _fetch_tenants(erp):
        tid = str(_row_value(r, "id", 0))
        billing = _get_billing_override(web_conn, tid)
        life = _get_lifecycle(web_conn, tid)
        out.append(
            _serialize_tenant(
                r,
                plans,
                billing,
                stripe_status=stripe_by_tenant.get(tid),
                onboarding_status=onboard_by_tenant.get(tid),
                lifecycle_row=life,
            )
        )
    return out


def _fetch_tenants(erp) -> list:
    has_rooms = _table_exists(erp, "hospitalityclaw_room")
    room_sql = (
        "(SELECT COUNT(*) FROM hospitalityclaw_room r WHERE r.tenant_id = t.id)"
        if has_rooms
        else "0"
    )
    join_company = _table_exists(erp, "company")
    if join_company:
        sql = f"""SELECT t.id, t.hotel_name, t.admin_email, t.timezone, t.country,
                         t.currency, t.subscription_tier, t.status, t.created_at,
                         c.name as company_name, {room_sql} as room_count
                  FROM pms_tenant t
                  LEFT JOIN company c ON c.id = t.company_id
                  ORDER BY t.created_at DESC"""
    else:
        sql = f"""SELECT t.id, t.hotel_name, t.admin_email, t.timezone, t.country,
                         t.currency, t.subscription_tier, t.status, t.created_at,
                         t.hotel_name as company_name, {room_sql} as room_count
                  FROM pms_tenant t
                  ORDER BY t.created_at DESC"""
    return erp.execute(sql).fetchall()


def _get_tenant_row(erp, tenant_id: str):
    if not _table_exists(erp, "pms_tenant"):
        return None
    return erp.execute(
        "SELECT * FROM pms_tenant WHERE id = ? OR company_id = ?",
        (tenant_id, tenant_id),
    ).fetchone()


def _set_hotel_users_status(tenant_id: str, status: str) -> None:
    conn = db.get_connection()
    try:
        cols = table_columns(conn, "webclaw_user")
        if "tenant_id" not in cols:
            return
        conn.execute(
            "UPDATE webclaw_user SET status = ? WHERE tenant_id = ?",
            (status, tenant_id),
        )
        if status != "active":
            conn.execute(
                """DELETE FROM webclaw_session
                   WHERE user_id IN (SELECT id FROM webclaw_user WHERE tenant_id = ?)""",
                (tenant_id,),
            )
        conn.commit()
    except Exception:
        pass


def _scalar(conn, sql: str, default=0):
    try:
        row = conn.execute(sql).fetchone()
        if not row:
            return default
        val = row[0]
        return default if val is None else val
    except Exception:
        return default


# ── Tenants ──────────────────────────────────────────────────────────────────

@router.get("/tenants")
@require_any_permission("platform.tenants.view", "platform.tenants.manage", "platform.admin")
async def list_tenants(request: Request):
    """Platform SuperAdmin: list every hotel tenant (no row isolation)."""
    web_conn = db.get_connection()
    erp = _erp()
    if not erp:
        return {"status": "ok", "tenants": [], "count": 0}
    try:
        if not _table_exists(erp, "pms_tenant"):
            return {"status": "ok", "tenants": [], "count": 0}
        tenants = _serialize_tenants_with_billing(erp, web_conn)
        return {"status": "ok", "tenants": tenants, "count": len(tenants)}
    except Exception:
        return {"status": "ok", "tenants": [], "count": 0}
    finally:
        erp.close()


@router.post("/tenants")
@require_any_permission("platform.tenants.manage", "platform.admin")
async def create_tenant(request: Request):
    """Provision a new hotel tenant (does not switch the SuperAdmin session)."""
    try:
        body = await request.json()
        req = CreateHotelTenantRequest(**body)
    except ValidationError as e:
        return JSONResponse({"status": "error", "message": str(e.errors()[0]["msg"])}, 400)
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request body"}, 400)

    from auth.onboarding import register_hotel

    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    result = await register_hotel(
        hotel_name=req.hotel_name,
        admin_email=req.admin_email,
        password=req.password,
        currency=req.currency,
        country=req.country,
        timezone_name=req.timezone,
        subscription_tier=PLAN_TO_TIER.get(req.plan_code, "starter"),
        ip=ip,
        user_agent=ua,
        onboarding_source="admin",
    )
    http_status = result.pop("http_status", 200)
    result.pop("access_token", None)
    result.pop("refresh_token", None)
    result.pop("user", None)
    if http_status != 200:
        return JSONResponse(result, http_status)
    tenant = result.get("tenant") or {}
    tenant["plan_code"] = req.plan_code
    tid = tenant.get("id") or tenant.get("tenant_id")
    if tid:
        _record_subscription_event(
            db.get_connection(),
            str(tid),
            "plan_change",
            f"Hotel onboarded on {req.plan_code} plan",
            req.admin_email,
        )
    return {"status": "ok", "tenant": tenant, "message": result.get("message")}


@router.patch("/tenants/{tenant_id}")
@require_any_permission("platform.tenants.manage", "platform.admin")
async def update_tenant(tenant_id: str, request: Request):
    """Update hotel metadata or assigned subscription plan."""
    try:
        body = await request.json()
        req = UpdateHotelTenantRequest(**body)
    except ValidationError as e:
        return JSONResponse({"status": "error", "message": str(e.errors()[0]["msg"])}, 400)
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request body"}, 400)

    erp = _erp()
    if not erp:
        return JSONResponse({"status": "error", "message": "Hotel database is not available"}, 503)
    try:
        row = _get_tenant_row(erp, tenant_id)
        if not row:
            return JSONResponse({"status": "error", "message": "Hotel tenant not found"}, 404)
        tid = _row_value(row, "id", 0)
        updates = []
        params: list = []
        if req.hotel_name:
            updates.append("hotel_name = ?")
            params.append(req.hotel_name.strip())
        if req.timezone:
            updates.append("timezone = ?")
            params.append(req.timezone.strip())
        if req.country:
            updates.append("country = ?")
            params.append(req.country.strip())
        if req.currency:
            updates.append("currency = ?")
            params.append(req.currency.strip().upper())
        if req.plan_code:
            web_conn = db.get_connection()
            plan = _get_plan(web_conn, req.plan_code)
            if not plan:
                return JSONResponse({"status": "error", "message": f"Unknown plan '{req.plan_code}'"}, 400)
            updates.append("subscription_tier = ?")
            params.append(PLAN_TO_TIER.get(req.plan_code, "starter"))
        if not updates:
            return JSONResponse({"status": "error", "message": "No fields to update"}, 400)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(tid)
        erp.execute(f"UPDATE pms_tenant SET {', '.join(updates)} WHERE id = ?", params)
        erp.commit()
        if req.plan_code:
            actor = getattr(request.state, "user", None)
            email = None
            if isinstance(actor, dict):
                email = actor.get("email")
            _record_subscription_event(
                db.get_connection(),
                str(tid),
                "plan_change",
                f"Plan set to {req.plan_code}",
                email,
            )
        refreshed = _get_tenant_row(erp, tid)
        plans = _load_plan_map(db.get_connection())
        billing = _get_billing_override(db.get_connection(), str(tid))
        listed = [
            t for t in (_serialize_tenant(r, plans, _get_billing_override(db.get_connection(), str(_row_value(r, "id", 0)))) for r in _fetch_tenants(erp))
            if t["id"] == tid
        ]
        tenant = listed[0] if listed else _serialize_tenant(refreshed, plans, billing)
        return {"status": "ok", "tenant": tenant}
    finally:
        erp.close()


@router.post("/tenants/{tenant_id}/pause")
@require_any_permission("platform.tenants.manage", "platform.admin")
async def pause_tenant(tenant_id: str, request: Request):
    """Suspend a hotel: block staff logins until resumed."""
    erp = _erp()
    if not erp:
        return JSONResponse({"status": "error", "message": "Hotel database is not available"}, 503)
    try:
        row = _get_tenant_row(erp, tenant_id)
        if not row:
            return JSONResponse({"status": "error", "message": "Hotel tenant not found"}, 404)
        tid = _row_value(row, "id", 0)
        erp.execute(
            "UPDATE pms_tenant SET status = 'suspended', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (tid,),
        )
        erp.commit()
        _set_hotel_users_status(tid, "disabled")
        try:
            _upsert_lifecycle(db.get_connection(), str(tid), "suspended")
        except Exception:
            pass
        return {"status": "ok", "message": "Hotel paused", "tenant_id": tid, "tenant_status": "suspended"}
    finally:
        erp.close()


@router.post("/tenants/{tenant_id}/resume")
@require_any_permission("platform.tenants.manage", "platform.admin")
async def resume_tenant(tenant_id: str, request: Request):
    """Re-activate a paused hotel."""
    erp = _erp()
    if not erp:
        return JSONResponse({"status": "error", "message": "Hotel database is not available"}, 503)
    try:
        row = _get_tenant_row(erp, tenant_id)
        if not row:
            return JSONResponse({"status": "error", "message": "Hotel tenant not found"}, 404)
        tid = _row_value(row, "id", 0)
        erp.execute(
            "UPDATE pms_tenant SET status = 'active', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (tid,),
        )
        erp.commit()
        _set_hotel_users_status(tid, "active")
        try:
            _upsert_lifecycle(db.get_connection(), str(tid), "active")
        except Exception:
            pass
        return {"status": "ok", "message": "Hotel resumed", "tenant_id": tid, "tenant_status": "active"}
    finally:
        erp.close()


@router.delete("/tenants/{tenant_id}")
@require_any_permission("platform.tenants.manage", "platform.admin")
async def delete_tenant(tenant_id: str, request: Request):
    """Soft-cancel a hotel (retain data until purge). Use POST .../purge for hard delete."""
    user = getattr(request.state, "user", None) or {}
    email = user.get("email")
    result = soft_cancel_tenant(tenant_id, actor_email=email)
    if not result.get("ok"):
        return JSONResponse(
            {"status": "error", "message": result.get("error")},
            result.get("http_status", 400),
        )
    return {
        "status": "ok",
        "message": "Hotel cancelled (data retained until purge)",
        "tenant_id": result["tenant_id"],
        "tenant_status": "cancelled",
        "lifecycle": "cancelled",
        "purge_after": result.get("purge_after"),
    }


@router.post("/tenants/{tenant_id}/cancel")
@require_any_permission("platform.tenants.manage", "platform.admin")
async def cancel_tenant(tenant_id: str, request: Request):
    """Alias for soft-cancel."""
    return await delete_tenant(tenant_id, request)


@router.post("/tenants/{tenant_id}/purge")
@require_any_permission("platform.tenants.manage", "platform.admin")
async def purge_tenant(tenant_id: str, request: Request):
    """Hard-delete a cancelled hotel registry row after retention."""
    user = getattr(request.state, "user", None) or {}
    result = purge_tenant_hard(tenant_id, actor_email=user.get("email"))
    if not result.get("ok"):
        return JSONResponse(
            {"status": "error", "message": result.get("error")},
            result.get("http_status", 400),
        )
    return {"status": "ok", "message": result.get("message"), "tenant_id": result["tenant_id"]}


@router.get("/tenants/{tenant_id}")
@require_any_permission("platform.tenants.view", "platform.tenants.manage", "platform.admin")
async def get_tenant_detail(tenant_id: str, request: Request):
    """Single hotel workspace: plan, usage vs limits, Stripe, recent support/billing."""
    web_conn = db.get_connection()
    erp = _erp()
    if not erp:
        return JSONResponse({"status": "error", "message": "Hotel database is not available"}, 503)
    try:
        if not _table_exists(erp, "pms_tenant"):
            return JSONResponse({"status": "error", "message": "Hotel tenant not found"}, 404)
        row = _get_tenant_row(erp, tenant_id)
        if not row:
            return JSONResponse({"status": "error", "message": "Hotel tenant not found"}, 404)

        plans = _load_plan_map(web_conn)
        tid = str(_row_value(row, "id", 0))
        billing = _get_billing_override(web_conn, tid)

        room_count = 0
        if _table_exists(erp, "hospitalityclaw_room"):
            try:
                room_count = int(
                    erp.execute(
                        "SELECT COUNT(*) FROM hospitalityclaw_room WHERE tenant_id = ?",
                        (tid,),
                    ).fetchone()[0]
                    or 0
                )
            except Exception:
                room_count = 0

        tenant = _serialize_tenant(row, plans, billing)
        tenant["room_count"] = room_count

        staff_count = 0
        try:
            cols = table_columns(web_conn, "webclaw_user")
            if "tenant_id" in cols:
                staff_count = int(
                    web_conn.execute(
                        "SELECT COUNT(*) FROM webclaw_user WHERE tenant_id = ? AND status != 'deleted'",
                        (tid,),
                    ).fetchone()[0]
                    or 0
                )
        except Exception:
            staff_count = 0

        max_rooms = tenant.get("max_rooms")
        max_staff = tenant.get("max_staff")
        usage = {
            "rooms": room_count,
            "max_rooms": max_rooms,
            "rooms_unlimited": not max_rooms or int(max_rooms or 0) <= 0,
            "rooms_over_limit": bool(max_rooms and int(max_rooms) > 0 and room_count > int(max_rooms)),
            "staff": staff_count,
            "max_staff": max_staff,
            "staff_unlimited": not max_staff or int(max_staff or 0) <= 0,
            "staff_over_limit": bool(max_staff and int(max_staff) > 0 and staff_count > int(max_staff)),
        }

        ensure_billing_tables(web_conn)
        stripe_row = None
        try:
            from stripe_billing import ensure_stripe_tables, get_stripe_customer_row

            ensure_stripe_tables(web_conn)
            stripe_row = get_stripe_customer_row(web_conn, tid)
        except Exception:
            stripe_row = None

        events = []
        try:
            for r in web_conn.execute(
                """SELECT id, tenant_id, event_type, detail, actor_email, created_at
                   FROM webclaw_subscription_event
                   WHERE tenant_id = ?
                   ORDER BY datetime(created_at) DESC LIMIT 20""",
                (tid,),
            ).fetchall():
                events.append(
                    {
                        "id": _row_value(r, "id", 0),
                        "tenant_id": _row_value(r, "tenant_id", 1),
                        "event_type": _row_value(r, "event_type", 2),
                        "detail": _row_value(r, "detail", 3),
                        "actor_email": _row_value(r, "actor_email", 4),
                        "created_at": _row_value(r, "created_at", 5),
                    }
                )
        except Exception:
            events = []

        tickets = []
        try:
            from admin_ops import ensure_ops_tables, list_tickets_for_tenant

            ensure_ops_tables(web_conn)
            tickets = list_tickets_for_tenant(web_conn, tid, limit=10)
        except Exception:
            tickets = []

        onboarding = None
        try:
            from admin_onboarding import (
                ensure_onboarding_tables,
                start_onboarding,
                sync_onboarding_progress,
            )

            ensure_onboarding_tables(web_conn)
            if not web_conn.execute(
                "SELECT 1 FROM webclaw_tenant_onboarding WHERE tenant_id = ?",
                (tid,),
            ).fetchone():
                start_onboarding(
                    web_conn,
                    tenant_id=tid,
                    hotel_name=tenant.get("hotel_name"),
                    admin_email=tenant.get("admin_email"),
                    source="admin",
                )
            onboarding = sync_onboarding_progress(web_conn, tid)
        except Exception:
            onboarding = None

        return {
            "status": "ok",
            "tenant": tenant,
            "usage": usage,
            "billing_override": billing,
            "stripe": stripe_row,
            "subscription_events": events,
            "tickets": tickets,
            "onboarding": onboarding,
        }
    finally:
        erp.close()


@router.post("/tenants/{tenant_id}/impersonate")
@require_any_permission("platform.support.manage", "platform.admin")
async def impersonate_tenant(tenant_id: str, request: Request):
    """Issue a hotel-scoped access token so SuperAdmin can operate as that property.

    Requires a support ``reason`` (min 10 chars). Session is short-lived and audited.
    """
    ctx = get_optional_tenant(request)
    if ctx is None or not ctx.user_id:
        return JSONResponse({"status": "error", "message": "Authentication required"}, 401)
    if getattr(ctx, "impersonating", False):
        return JSONResponse(
            {"status": "error", "message": "Already impersonating a hotel"},
            400,
        )

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    reason = str(body.get("reason") or "").strip()
    if len(reason) < 10:
        return JSONResponse(
            {
                "status": "error",
                "message": "Impersonation requires a support reason (at least 10 characters)",
            },
            400,
        )

    erp = _erp()
    if not erp:
        return JSONResponse({"status": "error", "message": "Hotel database is not available"}, 503)
    try:
        row = _get_tenant_row(erp, tenant_id)
        if not row:
            return JSONResponse({"status": "error", "message": "Hotel tenant not found"}, 404)
        tid = _row_value(row, "id", 0)
        hotel_name = _row_value(row, "hotel_name", 2) or _row_value(row, "hotel_name", 1)
        try:
            status = row["status"]
        except (KeyError, IndexError, TypeError):
            status = _row_value(row, "status", 8)
        if status == "cancelled":
            return JSONResponse({"status": "error", "message": "Cannot impersonate a cancelled hotel"}, 400)
        conn = db.get_connection()
        secret = get_signing_secret(conn)
        token = create_access_token(
            ctx.user_id,
            ctx.email or "",
            secret,
            tenant_id=tid,
            is_superadmin=True,
            impersonate_tenant_id=tid,
            impersonation_reason=reason,
        )
        try:
            from admin_ops import write_platform_audit

            write_platform_audit(
                conn,
                user_id=ctx.user_id,
                user_email=ctx.email,
                tenant_id=str(tid),
                action_category="security",
                action="impersonation_started",
                ip_address=request.client.host if request.client else None,
                detail=json.dumps(
                    {"hotel_name": hotel_name, "reason": reason},
                    default=str,
                ),
            )
        except Exception:
            pass
        return {
            "status": "ok",
            "access_token": token,
            "expires_in_minutes": 15,
            "user": {
                "user_id": ctx.user_id,
                "email": ctx.email,
                "full_name": hotel_name,
                "tenant_id": tid,
                "is_superadmin": False,
                "is_impersonating": True,
                "impersonated_hotel": hotel_name,
                "impersonation_reason": reason,
                "roles": [{"role_name": "Hotel Owner"}],
            },
            "tenant_id": tid,
            "hotel_name": hotel_name,
            "reason": reason,
        }
    finally:
        erp.close()


@router.get("/billing/overview")
@require_any_permission("platform.billing.manage", "platform.tenants.view", "platform.admin")
async def billing_overview(request: Request):
    """Past-due / failed payment queue and recent Stripe linkage for operators."""
    web_conn = db.get_connection()
    ensure_billing_tables(web_conn)
    try:
        from stripe_billing import ensure_stripe_tables

        ensure_stripe_tables(web_conn)
    except Exception:
        pass

    plans = _load_plan_map(web_conn)
    erp = _erp()
    tenant_by_id: dict[str, dict] = {}
    if erp:
        try:
            for t in _serialize_tenants_with_billing(erp, web_conn):
                tenant_by_id[str(t["id"])] = t
        finally:
            erp.close()

    at_risk: list[dict] = []
    try:
        rows = web_conn.execute(
            """SELECT tenant_id, stripe_customer_id, stripe_subscription_id, plan_code,
                      status, current_period_end, cancel_at_period_end, updated_at
               FROM webclaw_stripe_customer
               WHERE lower(status) IN ('past_due', 'unpaid', 'incomplete', 'incomplete_expired')
               ORDER BY datetime(updated_at) DESC"""
        ).fetchall()
        for r in rows:
            tid = str(_row_value(r, "tenant_id", 0))
            hotel = tenant_by_id.get(tid) or {}
            at_risk.append(
                {
                    "tenant_id": tid,
                    "hotel_name": hotel.get("hotel_name") or tid,
                    "admin_email": hotel.get("admin_email"),
                    "plan_code": _row_value(r, "plan_code", 3) or hotel.get("plan_code"),
                    "stripe_status": _row_value(r, "status", 4),
                    "stripe_customer_id": _row_value(r, "stripe_customer_id", 1),
                    "stripe_subscription_id": _row_value(r, "stripe_subscription_id", 2),
                    "current_period_end": _row_value(r, "current_period_end", 5),
                    "cancel_at_period_end": bool(_row_value(r, "cancel_at_period_end", 6)),
                    "updated_at": _row_value(r, "updated_at", 7),
                    "hotel_status": hotel.get("status"),
                }
            )
    except Exception:
        at_risk = []

    failed_events: list[dict] = []
    try:
        for r in web_conn.execute(
            """SELECT id, tenant_id, event_type, detail, actor_email, created_at
               FROM webclaw_subscription_event
               WHERE event_type IN (
                   'stripe_payment_failed', 'stripe_hotel_suspended', 'invoice.payment_failed'
               )
               ORDER BY datetime(created_at) DESC
               LIMIT 50"""
        ).fetchall():
            tid = str(_row_value(r, "tenant_id", 1))
            hotel = tenant_by_id.get(tid) or {}
            failed_events.append(
                {
                    "id": _row_value(r, "id", 0),
                    "tenant_id": tid,
                    "hotel_name": hotel.get("hotel_name") or tid,
                    "event_type": _row_value(r, "event_type", 2),
                    "detail": _row_value(r, "detail", 3),
                    "actor_email": _row_value(r, "actor_email", 4),
                    "created_at": _row_value(r, "created_at", 5),
                }
            )
    except Exception:
        failed_events = []

    # Trial ending soon (within 7 days)
    trials_ending: list[dict] = []
    try:
        for r in web_conn.execute(
            """SELECT tenant_id, trial_ends_at, discount_percent, discount_note
               FROM webclaw_tenant_billing_override
               WHERE trial_ends_at IS NOT NULL
                 AND datetime(trial_ends_at) <= datetime('now', '+7 days')
                 AND datetime(trial_ends_at) >= datetime('now')
               ORDER BY datetime(trial_ends_at) ASC"""
        ).fetchall():
            tid = str(_row_value(r, "tenant_id", 0))
            hotel = tenant_by_id.get(tid) or {}
            trials_ending.append(
                {
                    "tenant_id": tid,
                    "hotel_name": hotel.get("hotel_name") or tid,
                    "trial_ends_at": _row_value(r, "trial_ends_at", 1),
                    "plan_code": hotel.get("plan_code"),
                    "hotel_status": hotel.get("status"),
                }
            )
    except Exception:
        trials_ending = []

    return {
        "status": "ok",
        "at_risk": at_risk,
        "failed_events": failed_events,
        "trials_ending": trials_ending,
        "counts": {
            "at_risk": len(at_risk),
            "failed_events": len(failed_events),
            "trials_ending": len(trials_ending),
            "plans": len(plans),
        },
    }


@router.get("/billing/invoices")
@require_any_permission("platform.billing.manage", "platform.admin")
async def billing_invoices(request: Request, tenant_id: str, limit: int = 20):
    """List Stripe invoices for a hotel (requires Stripe customer link)."""
    web_conn = db.get_connection()
    try:
        from stripe_billing import list_invoices_for_tenant, platform_stripe_configured

        if not platform_stripe_configured(web_conn):
            return JSONResponse({"status": "error", "message": "Stripe is not configured"}, 503)
        invoices = list_invoices_for_tenant(web_conn, tenant_id, limit=limit)
        return {"status": "ok", "tenant_id": tenant_id, "invoices": invoices}
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 502)


@router.post("/billing/refunds")
@require_any_permission("platform.billing.manage", "platform.admin")
async def billing_refund(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid body"}, 400)
    web_conn = db.get_connection()
    user = getattr(request.state, "user", None) or {}
    try:
        from stripe_billing import create_refund, platform_stripe_configured

        if not platform_stripe_configured(web_conn):
            return JSONResponse({"status": "error", "message": "Stripe is not configured"}, 503)
        refund = create_refund(
            web_conn,
            payment_intent_id=body.get("payment_intent_id"),
            charge_id=body.get("charge_id"),
            amount_cents=body.get("amount_cents"),
            reason=body.get("reason"),
        )
        tid = body.get("tenant_id")
        if tid:
            _record_subscription_event(
                web_conn,
                str(tid),
                "refund_created",
                json.dumps(refund),
                user.get("email"),
            )
        return {"status": "ok", "refund": refund}
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 502)


@router.post("/billing/credits")
@require_any_permission("platform.billing.manage", "platform.admin")
async def billing_credit(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid body"}, 400)
    tenant_id = body.get("tenant_id")
    amount = body.get("amount_cents")
    if not tenant_id or amount is None:
        return JSONResponse(
            {"status": "error", "message": "tenant_id and amount_cents required"}, 400
        )
    web_conn = db.get_connection()
    user = getattr(request.state, "user", None) or {}
    try:
        from stripe_billing import create_customer_credit, platform_stripe_configured

        if not platform_stripe_configured(web_conn):
            return JSONResponse({"status": "error", "message": "Stripe is not configured"}, 503)
        credit = create_customer_credit(
            web_conn,
            str(tenant_id),
            amount_cents=int(amount),
            currency=body.get("currency") or "usd",
            description=body.get("description") or "Platform credit",
        )
        _record_subscription_event(
            web_conn,
            str(tenant_id),
            "credit_applied",
            json.dumps(credit),
            user.get("email"),
        )
        return {"status": "ok", "credit": credit}
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 502)


@router.post("/billing/coupons")
@require_any_permission("platform.billing.manage", "platform.admin")
async def billing_create_coupon(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid body"}, 400)
    code = (body.get("code") or "").strip()
    if not code:
        return JSONResponse({"status": "error", "message": "code required"}, 400)
    web_conn = db.get_connection()
    user = getattr(request.state, "user", None) or {}
    try:
        from stripe_billing import create_coupon, platform_stripe_configured

        if not platform_stripe_configured(web_conn):
            return JSONResponse({"status": "error", "message": "Stripe is not configured"}, 503)
        coupon = create_coupon(
            web_conn,
            code=code,
            percent_off=body.get("percent_off"),
            amount_off_cents=body.get("amount_off_cents"),
            currency=body.get("currency") or "usd",
            duration=body.get("duration") or "once",
        )
        _record_subscription_event(
            web_conn,
            body.get("tenant_id") or "platform",
            "coupon_created",
            json.dumps(coupon),
            user.get("email"),
        )
        return {"status": "ok", "coupon": coupon}
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 502)


@router.post("/billing/coupons/apply")
@require_any_permission("platform.billing.manage", "platform.admin")
async def billing_apply_coupon(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid body"}, 400)
    tenant_id = body.get("tenant_id")
    code = (body.get("code") or "").strip()
    if not tenant_id or not code:
        return JSONResponse({"status": "error", "message": "tenant_id and code required"}, 400)
    web_conn = db.get_connection()
    user = getattr(request.state, "user", None) or {}
    try:
        from stripe_billing import apply_coupon_to_tenant, platform_stripe_configured

        if not platform_stripe_configured(web_conn):
            return JSONResponse({"status": "error", "message": "Stripe is not configured"}, 503)
        result = apply_coupon_to_tenant(web_conn, str(tenant_id), code)
        _record_subscription_event(
            web_conn,
            str(tenant_id),
            "coupon_applied",
            json.dumps(result),
            user.get("email"),
        )
        return {"status": "ok", "result": result}
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 502)


# ── Subscription plans ───────────────────────────────────────────────────────

@router.get("/plans")
@require_any_permission("platform.billing.manage", "platform.tenants.view", "platform.admin")
async def list_plans(request: Request):
    conn = db.get_connection()
    ensure_subscription_plans(conn)
    rows = conn.execute(
        "SELECT * FROM webclaw_subscription_plan ORDER BY sort_order, name"
    ).fetchall()
    return {
        "status": "ok",
        "plans": [_plan_to_dict(r) for r in rows],
        "feature_catalog": FEATURE_CATALOG,
    }


@router.post("/plans")
@require_any_permission("platform.billing.manage", "platform.admin")
async def create_plan(request: Request):
    try:
        body = await request.json()
        req = SubscriptionPlanRequest(**body)
    except ValidationError as e:
        return JSONResponse({"status": "error", "message": str(e.errors()[0]["msg"])}, 400)
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request body"}, 400)

    conn = db.get_connection()
    ensure_subscription_plans(conn)
    existing = conn.execute(
        "SELECT id FROM webclaw_subscription_plan WHERE code = ?", (req.code,)
    ).fetchone()
    if existing:
        return JSONResponse({"status": "error", "message": f"Plan '{req.code}' already exists"}, 409)

    plan_id = str(uuid.uuid4())
    now = _now()
    features = req.features or dict(_DEFAULT_FEATURES.get(req.code, {}))
    conn.execute(
        """INSERT INTO webclaw_subscription_plan
           (id, code, name, billing_cycle, monthly_price, annual_price,
            max_rooms, max_staff, features, is_active, sort_order, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            plan_id, req.code, req.name, req.billing_cycle,
            str(req.monthly_price), str(req.annual_price), int(req.max_rooms),
            int(req.max_staff), json.dumps(features), 1 if req.is_active else 0, req.sort_order,
            now, now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM webclaw_subscription_plan WHERE id = ?", (plan_id,)).fetchone()
    return {"status": "ok", "plan": _plan_to_dict(row)}


@router.put("/plans/{plan_id}")
@require_any_permission("platform.billing.manage", "platform.admin")
async def update_plan(plan_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request body"}, 400)

    conn = db.get_connection()
    ensure_subscription_plans(conn)
    existing = conn.execute(
        "SELECT * FROM webclaw_subscription_plan WHERE id = ?", (plan_id,)
    ).fetchone()
    if not existing:
        return JSONResponse({"status": "error", "message": "Plan not found"}, 404)

    merged = _plan_to_dict(existing)
    merged.update({k: v for k, v in body.items() if v is not None})
    try:
        req = SubscriptionPlanRequest(
            code=merged["code"],
            name=merged["name"],
            billing_cycle=merged.get("billing_cycle", "monthly"),
            monthly_price=str(merged.get("monthly_price", "0")),
            annual_price=str(merged.get("annual_price", "0")),
            max_rooms=int(merged.get("max_rooms") or 0),
            max_staff=int(merged.get("max_staff") or 0),
            features=merged.get("features") or {},
            is_active=bool(merged.get("is_active", True)),
            sort_order=int(merged.get("sort_order") or 0),
        )
    except ValidationError as e:
        return JSONResponse({"status": "error", "message": str(e.errors()[0]["msg"])}, 400)

    conn.execute(
        """UPDATE webclaw_subscription_plan
           SET code=?, name=?, billing_cycle=?, monthly_price=?, annual_price=?,
               max_rooms=?, max_staff=?, features=?, is_active=?, sort_order=?, updated_at=?
           WHERE id=?""",
        (
            req.code, req.name, req.billing_cycle, str(req.monthly_price),
            str(req.annual_price), int(req.max_rooms), int(req.max_staff),
            json.dumps(req.features),
            1 if req.is_active else 0, req.sort_order, _now(), plan_id,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM webclaw_subscription_plan WHERE id = ?", (plan_id,)).fetchone()
    return {"status": "ok", "plan": _plan_to_dict(row)}


@router.delete("/plans/{plan_id}")
@require_any_permission("platform.billing.manage", "platform.admin")
async def delete_plan(plan_id: str, request: Request):
    conn = db.get_connection()
    ensure_subscription_plans(conn)
    row = conn.execute(
        "SELECT code FROM webclaw_subscription_plan WHERE id = ?", (plan_id,)
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "Plan not found"}, 404)
    code = row[0] if isinstance(row, tuple) else row["code"]
    if code in {"starter", "business", "enterprise"}:
        return JSONResponse(
            {"status": "error", "message": f"Cannot delete the built-in {code} plan"},
            403,
        )
    conn.execute("DELETE FROM webclaw_subscription_plan WHERE id = ?", (plan_id,))
    conn.commit()
    return {"status": "ok", "message": "Plan deleted"}


# ── Subscription history & billing overrides ─────────────────────────────────

@router.get("/tenants/{tenant_id}/subscription-history")
@require_any_permission("platform.billing.manage", "platform.tenants.view", "platform.admin")
async def tenant_subscription_history(tenant_id: str, request: Request):
    """Chronological subscription / billing events for a hotel tenant."""
    web_conn = db.get_connection()
    ensure_billing_tables(web_conn)
    erp = _erp()
    if erp:
        try:
            row = _get_tenant_row(erp, tenant_id)
            if not row:
                return JSONResponse({"status": "error", "message": "Hotel tenant not found"}, 404)
            tenant_id = str(_row_value(row, "id", 0))
        finally:
            erp.close()

    rows = web_conn.execute(
        """SELECT id, tenant_id, event_type, detail, actor_email, created_at
           FROM webclaw_subscription_event
           WHERE tenant_id = ?
           ORDER BY datetime(created_at) DESC
           LIMIT 100""",
        (tenant_id,),
    ).fetchall()
    events = [
        {
            "id": _row_value(r, "id", 0),
            "tenant_id": _row_value(r, "tenant_id", 1),
            "event_type": _row_value(r, "event_type", 2),
            "detail": _row_value(r, "detail", 3) or "",
            "actor_email": _row_value(r, "actor_email", 4),
            "created_at": _row_value(r, "created_at", 5),
        }
        for r in rows
    ]
    override = _get_billing_override(web_conn, tenant_id)
    return {"status": "ok", "events": events, "override": override, "tenant_id": tenant_id}


@router.post("/tenants/{tenant_id}/billing-override")
@require_any_permission("platform.billing.manage", "platform.admin")
async def apply_billing_override(tenant_id: str, request: Request):
    """Manual overrides: extend trial or apply / clear discount."""
    try:
        body = await request.json()
        req = BillingOverrideRequest(**body)
    except ValidationError as e:
        return JSONResponse({"status": "error", "message": str(e.errors()[0]["msg"])}, 400)
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request body"}, 400)

    erp = _erp()
    if not erp:
        return JSONResponse({"status": "error", "message": "Hotel database is not available"}, 503)
    try:
        row = _get_tenant_row(erp, tenant_id)
        if not row:
            return JSONResponse({"status": "error", "message": "Hotel tenant not found"}, 404)
        tid = str(_row_value(row, "id", 0))
    finally:
        erp.close()

    web_conn = db.get_connection()
    ensure_billing_tables(web_conn)
    current = _get_billing_override(web_conn, tid)
    actor = getattr(request.state, "user", None)
    actor_email = actor.get("email") if isinstance(actor, dict) else None

    trial_ends = current.get("trial_ends_at")
    discount = float(current.get("discount_percent") or 0)
    note = current.get("discount_note") or ""
    event_type = req.action
    detail = ""

    if req.action == "extend_trial":
        from datetime import timedelta

        base = datetime.now(timezone.utc)
        if trial_ends:
            try:
                parsed = datetime.fromisoformat(str(trial_ends).replace("Z", "+00:00"))
                if parsed > base:
                    base = parsed
            except Exception:
                pass
        trial_ends = (base + timedelta(days=req.days)).isoformat()
        detail = f"Trial extended by {req.days} day(s) → {trial_ends}"
    elif req.action == "apply_discount":
        discount = float(req.discount_percent)
        note = (req.note or "").strip() or note
        detail = f"Discount set to {discount}%" + (f" ({note})" if note else "")
    elif req.action == "clear_discount":
        discount = 0
        note = ""
        detail = "Discount cleared"
    elif req.action == "clear_trial":
        trial_ends = None
        detail = "Trial end cleared"

    web_conn.execute(
        """INSERT INTO webclaw_tenant_billing_override
           (tenant_id, trial_ends_at, discount_percent, discount_note, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(tenant_id) DO UPDATE SET
             trial_ends_at = excluded.trial_ends_at,
             discount_percent = excluded.discount_percent,
             discount_note = excluded.discount_note,
             updated_at = excluded.updated_at""",
        (tid, trial_ends, discount, note or None, _now()),
    )
    web_conn.commit()
    _record_subscription_event(web_conn, tid, event_type, detail, actor_email)
    override = _get_billing_override(web_conn, tid)
    return {"status": "ok", "override": override, "message": detail}


# ── Analytics ────────────────────────────────────────────────────────────────

def _friendly_activity_label(action: str, skill: str = "") -> tuple[str, str]:
    """Map raw audit/skill actions to platform event labels + kinds."""
    a = (action or "").lower()
    if "login" in a and ("fail" in a or "lock" in a):
        return "Failed login attempt", "security"
    if a in ("register-hotel", "create-tenant") or "register" in a and "hotel" in a:
        return "New hotel onboarded", "hotel"
    if "plan" in a and ("update" in a or "upgrade" in a or "change" in a):
        return "Subscription upgraded", "billing"
    if "payment" in a or a.startswith("add-payment") or a.startswith("submit-payment"):
        return "Payment processed", "billing"
    if a.startswith("pause") or "suspend" in a:
        return "Hotel suspended", "hotel"
    if a.startswith("resume"):
        return "Hotel resumed", "hotel"
    if skill.startswith("admin") or skill == "webclaw":
        return action.replace("-", " ").capitalize() or "Platform event", "system"
    return action.replace("-", " ").capitalize() or "Platform event", "system"


@router.get("/analytics")
@require_any_permission("platform.tenants.view", "platform.admin")
async def system_analytics(request: Request):
    """System-wide KPIs for the SuperAdmin global platform dashboard."""
    started = datetime.now(timezone.utc)
    web_conn = db.get_connection()
    plans = _load_plan_map(web_conn)
    erp = _erp()

    hotels = {"total": 0, "active": 0, "suspended": 0, "cancelled": 0, "onboarding": 0}
    bookings = {"total": 0, "active": 0, "by_status": {}}
    booking_revenue = 0.0
    total_rooms = 0
    tenants_for_mrr: list[str] = []
    activity: list[dict] = []
    db_ok = True

    if erp:
        try:
            if _table_exists(erp, "pms_tenant"):
                hotels["total"] = int(_scalar(erp, "SELECT COUNT(*) FROM pms_tenant"))
                hotels["active"] = int(
                    _scalar(erp, "SELECT COUNT(*) FROM pms_tenant WHERE status = 'active'")
                )
                hotels["suspended"] = int(
                    _scalar(erp, "SELECT COUNT(*) FROM pms_tenant WHERE status = 'suspended'")
                )
                hotels["cancelled"] = int(
                    _scalar(erp, "SELECT COUNT(*) FROM pms_tenant WHERE status = 'cancelled'")
                )
                # Onboarding: real pipeline (in_progress / ready / blocked)
                try:
                    from admin_onboarding import count_onboarding_in_progress

                    hotels["onboarding"] = count_onboarding_in_progress(web_conn)
                except Exception:
                    hotels["onboarding"] = 0

                for r in erp.execute(
                    "SELECT subscription_tier FROM pms_tenant WHERE status = 'active'"
                ).fetchall():
                    tenants_for_mrr.append(
                        str(r[0] if not hasattr(r, "keys") else r["subscription_tier"])
                    )

                # Recent hotel onboard events
                try:
                    for r in erp.execute(
                        """SELECT hotel_name, created_at FROM pms_tenant
                           ORDER BY datetime(created_at) DESC LIMIT 8"""
                    ).fetchall():
                        name = _row_value(r, "hotel_name", 0)
                        created = _row_value(r, "created_at", 1)
                        activity.append(
                            {
                                "id": f"hotel-{name}-{created}",
                                "kind": "hotel",
                                "label": "New hotel onboarded",
                                "detail": str(name or "Hotel"),
                                "created_at": str(created or ""),
                            }
                        )
                except Exception:
                    pass

            if _table_exists(erp, "hospitalityclaw_room"):
                total_rooms = int(_scalar(erp, "SELECT COUNT(*) FROM hospitalityclaw_room"))

            if _table_exists(erp, "hospitalityclaw_reservation"):
                bookings["total"] = int(
                    _scalar(
                        erp,
                        "SELECT COUNT(*) FROM hospitalityclaw_reservation WHERE status != 'cancelled'",
                    )
                )
                bookings["active"] = int(
                    _scalar(
                        erp,
                        """SELECT COUNT(*) FROM hospitalityclaw_reservation
                           WHERE status IN ('reserved', 'checked_in')""",
                    )
                )
                rows = erp.execute(
                    """SELECT status, COUNT(*) as n
                       FROM hospitalityclaw_reservation GROUP BY status"""
                ).fetchall()
                bookings["by_status"] = {
                    str(_row_value(r, "status", 0)): int(_row_value(r, "n", 1) or 0)
                    for r in rows
                }

            if _table_exists(erp, "hospitalityclaw_folio_charge"):
                booking_revenue = float(
                    _scalar(
                        erp,
                        """SELECT COALESCE(SUM(CAST(amount AS REAL)), 0)
                           FROM hospitalityclaw_folio_charge
                           WHERE charge_type != 'payment'""",
                    )
                )
                try:
                    for r in erp.execute(
                        """SELECT amount, created_at, charge_type
                           FROM hospitalityclaw_folio_charge
                           WHERE charge_type = 'payment'
                           ORDER BY datetime(created_at) DESC LIMIT 5"""
                    ).fetchall():
                        amount = _row_value(r, "amount", 0)
                        created = _row_value(r, "created_at", 1)
                        activity.append(
                            {
                                "id": f"pay-{created}-{amount}",
                                "kind": "billing",
                                "label": "Payment processed",
                                "detail": f"${amount}",
                                "created_at": str(created or ""),
                            }
                        )
                except Exception:
                    pass

            # Skill audit trail (erpclaw) for upgrades / payments / misc
            if _table_exists(erp, "audit_log"):
                try:
                    for r in erp.execute(
                        """SELECT skill, action, user_id, timestamp as created_at
                           FROM audit_log
                           ORDER BY timestamp DESC LIMIT 20"""
                    ).fetchall():
                        action = str(_row_value(r, "action", 1) or "")
                        skill = str(_row_value(r, "skill", 0) or "")
                        label, kind = _friendly_activity_label(action, skill)
                        if kind in ("billing", "hotel", "security") or any(
                            k in action.lower()
                            for k in ("payment", "plan", "subscription", "tenant", "hotel")
                        ):
                            activity.append(
                                {
                                    "id": f"audit-{skill}-{action}-{_row_value(r, 'created_at', 3)}",
                                    "kind": kind,
                                    "label": label,
                                    "detail": f"{skill} · {action}" if skill else action,
                                    "created_at": str(_row_value(r, "created_at", 3) or ""),
                                }
                            )
                except Exception:
                    pass
        except Exception:
            db_ok = False
        finally:
            try:
                erp.close()
            except Exception:
                pass
    else:
        db_ok = False

    # Active hotel admin users (tenant-scoped accounts)
    active_admin_users = 0
    try:
        if _table_exists(web_conn, "webclaw_user"):
            cols = table_columns(web_conn, "webclaw_user")
            if "tenant_id" in cols:
                active_admin_users = int(
                    _scalar(
                        web_conn,
                        """SELECT COUNT(*) FROM webclaw_user
                           WHERE tenant_id IS NOT NULL AND tenant_id != ''
                             AND status = 'active'""",
                    )
                )
            # Failed login attempts as security events
            if "failed_login_attempts" in cols:
                for r in web_conn.execute(
                    """SELECT email, failed_login_attempts, locked_until
                       FROM webclaw_user
                       WHERE failed_login_attempts > 0
                       ORDER BY failed_login_attempts DESC LIMIT 5"""
                ).fetchall():
                    email = _row_value(r, "email", 0)
                    attempts = _row_value(r, "failed_login_attempts", 1)
                    locked = _row_value(r, "locked_until", 2)
                    activity.append(
                        {
                            "id": f"login-fail-{email}",
                            "kind": "security",
                            "label": "Failed login attempt",
                            "detail": f"{email} · {attempts} attempt(s)"
                            + (" · locked" if locked else ""),
                            "created_at": _now(),
                        }
                    )
        if _table_exists(web_conn, "audit_log"):
            for r in web_conn.execute(
                """SELECT skill, action, user_id,
                          COALESCE(created_at, timestamp) as created_at
                   FROM audit_log
                   ORDER BY COALESCE(created_at, timestamp) DESC LIMIT 15"""
            ).fetchall():
                action = str(_row_value(r, "action", 1) or "")
                skill = str(_row_value(r, "skill", 0) or "")
                label, kind = _friendly_activity_label(action, skill)
                activity.append(
                    {
                        "id": f"web-audit-{skill}-{action}-{_row_value(r, 'created_at', 3)}",
                        "kind": kind,
                        "label": label,
                        "detail": skill or action,
                        "created_at": str(_row_value(r, "created_at", 3) or ""),
                    }
                )
    except Exception:
        pass

    subscription_mrr = 0.0
    for tier in tenants_for_mrr:
        code = TIER_TO_PLAN.get(str(tier).lower(), "starter")
        plan = plans.get(code) or {}
        try:
            subscription_mrr += float(plan.get("monthly_price") or 0)
        except (TypeError, ValueError):
            pass

    # Deduplicate + sort activity
    seen: set[str] = set()
    deduped: list[dict] = []
    for ev in activity:
        key = f"{ev.get('label')}|{ev.get('detail')}|{ev.get('created_at')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ev)

    def _sort_key(ev: dict) -> str:
        return str(ev.get("created_at") or "")

    deduped.sort(key=_sort_key, reverse=True)
    deduped = deduped[:20]

    elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    workers_ok = db_ok  # background workers share the same process health for now

    # P0 dashboard depth: signups, churn, billing risk, ARR
    new_signups_30d = 0
    churn_30d = 0
    failed_payments_open = 0
    trials_ending_7d = 0
    past_due_hotels = 0
    stripe_ok = False
    try:
        ensure_billing_tables(web_conn)
        # Signups last 30d from lifecycle created events or ERP created_at
        erp2 = _erp()
        if erp2:
            try:
                if _table_exists(erp2, "pms_tenant"):
                    try:
                        new_signups_30d = int(
                            _scalar(
                                erp2,
                                """SELECT COUNT(*) FROM pms_tenant
                                   WHERE datetime(created_at) >= datetime('now', '-30 days')""",
                            )
                        )
                    except Exception:
                        new_signups_30d = 0
            finally:
                erp2.close()
        churn_30d = int(
            _scalar(
                web_conn,
                """SELECT COUNT(*) FROM webclaw_subscription_event
                   WHERE event_type IN ('hotel_cancelled', 'stripe_hotel_suspended')
                     AND datetime(created_at) >= datetime('now', '-30 days')""",
            )
            or 0
        )
        failed_payments_open = int(
            _scalar(
                web_conn,
                """SELECT COUNT(*) FROM webclaw_subscription_event
                   WHERE event_type IN ('stripe_payment_failed', 'invoice.payment_failed')
                     AND datetime(created_at) >= datetime('now', '-30 days')""",
            )
            or 0
        )
        trials_ending_7d = int(
            _scalar(
                web_conn,
                """SELECT COUNT(*) FROM webclaw_tenant_billing_override
                   WHERE trial_ends_at IS NOT NULL
                     AND datetime(trial_ends_at) <= datetime('now', '+7 days')
                     AND datetime(trial_ends_at) >= datetime('now')""",
            )
            or 0
        )
        try:
            from stripe_billing import ensure_stripe_tables, platform_stripe_configured

            ensure_stripe_tables(web_conn)
            stripe_ok = platform_stripe_configured(web_conn)
            past_due_hotels = int(
                _scalar(
                    web_conn,
                    """SELECT COUNT(*) FROM webclaw_stripe_customer
                       WHERE lower(status) IN ('past_due', 'unpaid', 'incomplete')""",
                )
                or 0
            )
        except Exception:
            stripe_ok = False
    except Exception:
        pass

    subscription_arr = round(subscription_mrr * 12, 2)
    health_degraded = (
        (not db_ok)
        or elapsed_ms >= 2000
        or past_due_hotels > 0
        or failed_payments_open > 5
    )
    health_status = "degraded" if health_degraded else "operational"
    health_bits = []
    if not db_ok:
        health_bits.append("database down")
    if elapsed_ms >= 2000:
        health_bits.append("slow API")
    if past_due_hotels:
        health_bits.append(f"{past_due_hotels} past-due")
    if failed_payments_open > 5:
        health_bits.append("payment failures")

    return {
        "status": "ok",
        "analytics": {
            "active_hotels": hotels["active"],
            "total_hotels": hotels["total"],
            "suspended_hotels": hotels["suspended"],
            "cancelled_hotels": hotels["cancelled"],
            "onboarding_hotels": hotels["onboarding"],
            "active_subscriptions": hotels["active"],
            "revenue": round(booking_revenue, 2),
            "subscription_mrr": round(subscription_mrr, 2),
            "subscription_arr": subscription_arr,
            "new_signups_30d": new_signups_30d,
            "churn_30d": churn_30d,
            "failed_payments_open": failed_payments_open,
            "trials_ending_7d": trials_ending_7d,
            "past_due_hotels": past_due_hotels,
            "booking_count": bookings["total"],
            "active_bookings": bookings["active"],
            "bookings_by_status": bookings["by_status"],
            "total_rooms": total_rooms,
            "active_admin_users": active_admin_users,
            "activity": deduped,
            "health": {
                "database": "ok" if db_ok else "down",
                "api_latency_ms": elapsed_ms,
                "workers": "ok" if workers_ok else "degraded",
                "stripe": "ok" if stripe_ok else "unconfigured",
                "past_due_hotels": past_due_hotels,
                "status": health_status,
                "label": (
                    "All Systems Operational"
                    if health_status == "operational"
                    else ("Degraded: " + ", ".join(health_bits) if health_bits else "Degraded Performance")
                ),
            },
        },
    }


def _cohort_rows(
    *,
    dimension: str = "plan",
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Aggregate hotels by plan / country / partner / signup month."""
    web_conn = db.get_connection()
    erp = _erp()
    if not erp:
        return []
    dimension = (dimension or "plan").strip().lower()
    if dimension not in {"plan", "country", "partner", "month"}:
        dimension = "plan"

    partner_by_tenant: dict[str, str] = {}
    try:
        from admin_growth import ensure_growth_tables

        ensure_growth_tables(web_conn)
        for r in web_conn.execute(
            """SELECT pt.tenant_id, p.name
               FROM webclaw_partner_tenant pt
               JOIN webclaw_partner p ON p.id = pt.partner_id"""
        ).fetchall():
            tid = str(r[0] if isinstance(r, tuple) else r["tenant_id"])
            partner_by_tenant[tid] = str(r[1] if isinstance(r, tuple) else r["name"])
    except Exception:
        pass

    buckets: dict[str, dict] = {}
    try:
        rows = erp.execute(
            """SELECT id, hotel_name, country, subscription_tier, status, created_at
               FROM pms_tenant"""
        ).fetchall()
        for r in rows:
            tid = str(_row_value(r, "id", 0))
            country = str(_row_value(r, "country", 2) or "UNSET")
            tier = str(_row_value(r, "subscription_tier", 3) or "starter").lower()
            status = str(_row_value(r, "status", 4) or "unknown")
            created = str(_row_value(r, "created_at", 5) or "")
            plan = TIER_TO_PLAN.get(tier, "starter")

            if date_from and created and created[:10] < date_from[:10]:
                continue
            if date_to and created and created[:10] > date_to[:10]:
                continue

            if dimension == "plan":
                key = plan
            elif dimension == "country":
                key = country.upper() if country else "UNSET"
            elif dimension == "partner":
                key = partner_by_tenant.get(tid) or "Direct"
            else:
                key = created[:7] if len(created) >= 7 else "unknown"

            b = buckets.setdefault(
                key,
                {
                    "key": key,
                    "hotels": 0,
                    "active": 0,
                    "suspended": 0,
                    "cancelled": 0,
                    "churned": 0,
                },
            )
            b["hotels"] += 1
            if status == "active":
                b["active"] += 1
            elif status == "suspended":
                b["suspended"] += 1
            elif status == "cancelled":
                b["cancelled"] += 1
                b["churned"] += 1
    finally:
        erp.close()

    out = sorted(buckets.values(), key=lambda x: (-x["hotels"], x["key"]))
    for b in out:
        total = b["hotels"] or 1
        b["churn_rate"] = round(100.0 * b["churned"] / total, 2)
        b["active_rate"] = round(100.0 * b["active"] / total, 2)
    return out


@router.get("/analytics/cohorts")
@require_any_permission("platform.tenants.view", "platform.admin")
async def analytics_cohorts(
    request: Request,
    dimension: str = "plan",
    date_from: str | None = None,
    date_to: str | None = None,
):
    rows = _cohort_rows(dimension=dimension, date_from=date_from, date_to=date_to)
    return {
        "status": "ok",
        "dimension": dimension,
        "date_from": date_from,
        "date_to": date_to,
        "cohorts": rows,
    }


@router.get("/analytics/export.csv")
@require_any_permission("platform.tenants.view", "platform.admin")
async def analytics_export_csv(
    request: Request,
    dimension: str = "plan",
    date_from: str | None = None,
    date_to: str | None = None,
):
    import csv
    import io

    rows = _cohort_rows(dimension=dimension, date_from=date_from, date_to=date_to)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "key",
            "hotels",
            "active",
            "suspended",
            "cancelled",
            "churned",
            "churn_rate",
            "active_rate",
        ],
    )
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    payload = buf.getvalue().encode("utf-8")
    return StreamingResponse(
        iter([payload]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="platform-cohorts-{dimension}.csv"'
        },
    )
