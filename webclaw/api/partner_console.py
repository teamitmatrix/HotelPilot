"""Partner / reseller scoped console — portfolio of linked hotels."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db
from auth.passwords import hash_password
from db_access import table_columns
from rbac import require_any_permission

router = APIRouter(prefix="/api/v1/partner", tags=["partner-console"])
admin_extra = APIRouter(prefix="/api/v1/admin", tags=["admin-partner-console"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r, key: str, idx: int = 0):
    if r is None:
        return None
    if hasattr(r, "keys"):
        try:
            return r[key]
        except Exception:
            return None
    return r[idx] if idx < len(r) else None


def partner_for_email(conn, email: str) -> dict | None:
    from admin_growth import ensure_growth_tables

    ensure_growth_tables(conn)
    email = (email or "").strip().lower()
    if not email:
        return None
    r = conn.execute(
        """SELECT id, name, contact_email, commission_percent, status, notes
           FROM webclaw_partner
           WHERE lower(contact_email) = ? AND status = 'active'""",
        (email,),
    ).fetchone()
    if not r:
        return None
    return {
        "id": _row(r, "id", 0),
        "name": _row(r, "name", 1),
        "contact_email": _row(r, "contact_email", 2),
        "commission_percent": float(_row(r, "commission_percent", 3) or 0),
        "status": _row(r, "status", 4),
        "notes": _row(r, "notes", 5) or "",
    }


def partner_for_user(conn, user_id: str, email: str | None = None) -> dict | None:
    """Resolve partner by linked user_id column or contact_email match."""
    from admin_growth import ensure_growth_tables

    ensure_growth_tables(conn)
    cols = set(table_columns(conn, "webclaw_partner") or [])
    if "user_id" in cols and user_id:
        r = conn.execute(
            """SELECT id, name, contact_email, commission_percent, status, notes
               FROM webclaw_partner WHERE user_id = ? AND status = 'active'""",
            (user_id,),
        ).fetchone()
        if r:
            return {
                "id": _row(r, "id", 0),
                "name": _row(r, "name", 1),
                "contact_email": _row(r, "contact_email", 2),
                "commission_percent": float(_row(r, "commission_percent", 3) or 0),
                "status": _row(r, "status", 4),
                "notes": _row(r, "notes", 5) or "",
            }
    if email:
        return partner_for_email(conn, email)
    return None


def _require_partner(request: Request) -> tuple[dict, dict] | JSONResponse:
    """Return (partner, actor) or an error response."""
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return JSONResponse({"status": "error", "message": "Authentication required"}, 401)
    from auth.jwt_utils import get_signing_secret, verify_token

    conn = db.get_connection()
    secret = get_signing_secret(conn)
    payload = verify_token(auth[7:], secret, expected_type="access")
    if not payload:
        return JSONResponse({"status": "error", "message": "Invalid or expired token"}, 401)
    user_id = payload["sub"]
    email = payload.get("email") or ""
    if not email:
        u = conn.execute(
            "SELECT email FROM webclaw_user WHERE id = ?", (user_id,)
        ).fetchone()
        email = str(_row(u, "email", 0) or "")
    partner = partner_for_user(conn, user_id, email)
    if not partner:
        return JSONResponse(
            {"status": "error", "message": "No active partner console for this account"},
            403,
        )
    return partner, {"user_id": user_id, "email": email}


def _linked_tenant_ids(conn, partner_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT tenant_id FROM webclaw_partner_tenant WHERE partner_id = ?",
        (partner_id,),
    ).fetchall()
    return [str(_row(r, "tenant_id", 0)) for r in rows]


def _partner_hotels(partner_id: str) -> list[dict]:
    from admin_growth import ensure_growth_tables
    from admin_platform import _erp as erp_fn, _serialize_tenants_with_billing

    conn = db.get_connection()
    ensure_growth_tables(conn)
    tids = set(_linked_tenant_ids(conn, partner_id))
    if not tids:
        return []
    erp = erp_fn()
    if not erp:
        return [
            {"id": t, "hotel_name": t, "status": "unknown", "plan_code": "starter"}
            for t in sorted(tids)
        ]
    try:
        hotels = _serialize_tenants_with_billing(erp, conn)
        matched = [h for h in hotels if str(h.get("id")) in tids]
        found = {str(h.get("id")) for h in matched}
        for t in tids - found:
            matched.append(
                {
                    "id": t,
                    "hotel_name": t,
                    "status": "linked",
                    "plan_code": "starter",
                    "country": None,
                }
            )
        return matched
    finally:
        erp.close()


@router.get("/me")
async def partner_me(request: Request):
    result = _require_partner(request)
    if isinstance(result, JSONResponse):
        return result
    partner, actor = result
    hotels = _partner_hotels(partner["id"])
    active = sum(1 for h in hotels if h.get("status") == "active")
    return {
        "status": "ok",
        "partner": partner,
        "actor": actor,
        "stats": {
            "tenant_count": len(hotels),
            "active_hotels": active,
            "commission_percent": partner["commission_percent"],
        },
    }


@router.get("/tenants")
async def partner_tenants(request: Request):
    result = _require_partner(request)
    if isinstance(result, JSONResponse):
        return result
    partner, _ = result
    hotels = _partner_hotels(partner["id"])
    return {"status": "ok", "partner_id": partner["id"], "tenants": hotels}


@router.get("/analytics")
async def partner_analytics(request: Request):
    result = _require_partner(request)
    if isinstance(result, JSONResponse):
        return result
    partner, _ = result
    hotels = _partner_hotels(partner["id"])
    by_plan: dict[str, int] = {}
    by_country: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for h in hotels:
        plan = str(h.get("plan_code") or "starter")
        country = str(h.get("country") or "UNSET")
        status = str(h.get("status") or "unknown")
        by_plan[plan] = by_plan.get(plan, 0) + 1
        by_country[country] = by_country.get(country, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "status": "ok",
        "partner_id": partner["id"],
        "analytics": {
            "tenant_count": len(hotels),
            "by_plan": by_plan,
            "by_country": by_country,
            "by_status": by_status,
            "estimated_commission_share_percent": partner["commission_percent"],
        },
    }


@admin_extra.post("/partners/{partner_id}/provision-user")
@require_any_permission("platform.billing.manage", "platform.admin")
async def provision_partner_user(partner_id: str, request: Request):
    """Create/link a console user for the partner contact_email."""
    from admin_growth import ensure_growth_tables, _row as grow_row, _audit

    conn = db.get_connection()
    ensure_growth_tables(conn)
    # Ensure user_id column
    cols = set(table_columns(conn, "webclaw_partner") or [])
    if "user_id" not in cols:
        try:
            conn.execute("ALTER TABLE webclaw_partner ADD COLUMN user_id TEXT")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass

    partner = conn.execute(
        "SELECT * FROM webclaw_partner WHERE id = ?", (partner_id,)
    ).fetchone()
    if not partner:
        return JSONResponse({"status": "error", "message": "Partner not found"}, 404)
    email = str(grow_row(partner, "contact_email", 2) or "").strip().lower()
    name = str(grow_row(partner, "name", 1) or "Partner")
    if not email or "@" not in email:
        return JSONResponse({"status": "error", "message": "Partner has no contact_email"}, 400)

    existing = conn.execute(
        "SELECT id FROM webclaw_user WHERE email = ?", (email,)
    ).fetchone()
    if existing:
        user_id = str(grow_row(existing, "id", 0))
    else:
        user_id = str(uuid.uuid4())
        temp = uuid.uuid4().hex[:10] + "Aa1!"
        conn.execute(
            """INSERT INTO webclaw_user
               (id, username, email, full_name, password_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?)""",
            (
                user_id,
                email.split("@")[0][:64],
                email,
                name,
                hash_password(temp),
                _now(),
            ),
        )
    # Assign Partner Admin role if present
    role = conn.execute(
        "SELECT id FROM webclaw_role WHERE name IN ('Partner Admin', 'PARTNER_ADMIN') LIMIT 1"
    ).fetchone()
    if role:
        rid = grow_row(role, "id", 0)
        link = conn.execute(
            "SELECT 1 FROM webclaw_user_role WHERE user_id = ? AND role_id = ?",
            (user_id, rid),
        ).fetchone()
        if not link:
            conn.execute(
                "INSERT INTO webclaw_user_role (id, user_id, role_id) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), user_id, rid),
            )
    conn.execute(
        "UPDATE webclaw_partner SET user_id = ?, updated_at = ? WHERE id = ?",
        (user_id, _now(), partner_id),
    )
    conn.commit()
    _audit(conn, request, "partner", "provision_user", f"{partner_id}:{user_id}", None)
    return {"status": "ok", "partner_id": partner_id, "user_id": user_id, "email": email}
