"""Owner branch (property) management under a hotel group.

GET  /api/v1/owner/properties  — list branches in the caller's group
POST /api/v1/owner/properties  — register a new branch + CoA cost center
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

import db
from auth.deps import get_current_user
from property_scope import (
    attach_property_to_group,
    backfill_groups_for_existing_properties,
    create_group,
    ensure_group_schema_erp,
    ensure_group_schema_webclaw,
    group_id_for_property,
    list_properties_in_group,
    set_accepting_bookings,
    sync_user_group_ids,
)
from rbac import require_permission
from skills.executor import execute_skill
from tenant import TenantContext, TenantIsolationError, upsert_membership

router = APIRouter(prefix="/api/v1/owner", tags=["owner-properties"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ONBOARD_TIMEOUT = 180


def _require_owner_ctx(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if not ctx or not ctx.user_id:
        raise TenantIsolationError("Authentication required", status_code=401)
    if ctx.is_superadmin and not ctx.impersonating:
        raise TenantIsolationError(
            "Use platform admin APIs for SuperAdmin property management",
            status_code=403,
        )
    return ctx


def _erp():
    conn = db.get_skill_db("erpclaw")
    if not conn:
        raise TenantIsolationError("ERP database unavailable", status_code=503)
    return conn


def _ensure_group_for_owner(web_conn, erp_conn, ctx: TenantContext) -> str:
    """Return group_id, creating/backfilling if the owner only has a legacy property."""
    ensure_group_schema_erp(erp_conn)
    ensure_group_schema_webclaw(web_conn)
    backfill_groups_for_existing_properties(erp_conn)
    sync_user_group_ids(web_conn, erp_conn)

    gid = ctx.group_id
    if not gid and ctx.tenant_id:
        gid = group_id_for_property(erp_conn, ctx.tenant_id)
    if not gid and ctx.tenant_id:
        # Last resort: create group and attach home property.
        home_name = "Hotel Group"
        row = erp_conn.execute(
            "SELECT hotel_name, admin_email FROM pms_tenant WHERE id = ? OR company_id = ?",
            (ctx.tenant_id, ctx.tenant_id),
        ).fetchone()
        if row:
            home_name = (row["hotel_name"] if hasattr(row, "keys") else row[0]) or home_name
            email = row["admin_email"] if hasattr(row, "keys") else (row[1] if len(row) > 1 else None)
        else:
            email = ctx.email
        gid = create_group(
            erp_conn,
            name=f"{home_name} Group",
            admin_email=email or ctx.email,
        )
        attach_property_to_group(erp_conn, ctx.tenant_id, gid)
    if not gid:
        raise TenantIsolationError(
            "No group_id for this account; register a hotel first",
            status_code=400,
        )
    web_conn.execute(
        "UPDATE webclaw_user SET group_id = ? WHERE id = ?",
        (gid, ctx.user_id),
    )
    web_conn.commit()
    return gid


def _validate_timezone(tz: str) -> str | None:
    try:
        ZoneInfo(tz)
        return None
    except ZoneInfoNotFoundError:
        return f"Unknown timezone: {tz}"


@router.get("/properties")
@require_permission("hotel.manage")
async def list_owner_properties(
    request: Request,
    user=Depends(get_current_user),
):
    """List all hotel branches under the owner's group_id."""
    ctx = _require_owner_ctx(request)

    web = db.get_connection()
    erp = _erp()
    try:
        gid = _ensure_group_for_owner(web, erp, ctx)
        props = list_properties_in_group(erp, gid)
        group = erp.execute(
            "SELECT id, name, admin_email, status FROM pms_group WHERE id = ?",
            (gid,),
        ).fetchone()
        group_info = (
            {k: group[k] for k in group.keys()}
            if group is not None and hasattr(group, "keys")
            else {"id": gid, "name": None, "admin_email": None, "status": "active"}
        )
        return {
            "status": "ok",
            "group_id": gid,
            "group": group_info,
            "active_property_id": ctx.active_property_id,
            "scope_all_properties": ctx.scope_all_properties,
            "properties": props,
            "total_count": len(props),
        }
    finally:
        erp.close()


@router.post("/properties")
@require_permission("hotel.manage")
async def create_owner_property(
    request: Request,
    user=Depends(get_current_user),
):
    """Register a new branch under the owner's group and seed CoA + hospitality."""
    ctx = _require_owner_ctx(request)

    body = await request.json()
    name = str(body.get("name") or body.get("hotel_name") or "").strip()
    if not name:
        return JSONResponse(
            {"status": "error", "message": "name is required"},
            status_code=400,
        )
    timezone_name = str(body.get("timezone") or "UTC").strip() or "UTC"
    tz_err = _validate_timezone(timezone_name)
    if tz_err:
        return JSONResponse({"status": "error", "message": tz_err}, status_code=400)

    currency = str(body.get("currency") or "USD").strip().upper() or "USD"
    country = str(body.get("country") or "United States").strip() or "United States"
    address = str(body.get("address") or "").strip() or None
    tax_template = str(body.get("tax_template") or body.get("chart_template") or "us_gaap").strip()
    admin_email = str(body.get("admin_email") or ctx.email or "").strip() or None

    web = db.get_connection()
    erp = _erp()
    try:
        gid = _ensure_group_for_owner(web, erp, ctx)
        property_id = str(uuid.uuid4())

        # 1) ERP company + default cost center (setup-company)
        setup = await execute_skill(
            "erpclaw",
            "setup-company",
            {
                "company-id": property_id,
                "name": name,
                "currency": currency,
                "country": country,
                "timezone": timezone_name,
                "admin-email": admin_email or "",
            },
            timeout=ONBOARD_TIMEOUT,
            tenant_id=property_id,
            is_superadmin=True,
        )
        if setup.get("status") != "ok":
            return JSONResponse(
                {
                    "status": "error",
                    "message": setup.get("message") or "setup-company failed",
                    "detail": setup,
                },
                status_code=500,
            )

        # Attach to parent group (setup-company may have created a 1:1 group;
        # force the owner's group).
        attach_property_to_group(erp, property_id, gid)

        # Rename default cost center under the parent group ledger narrative.
        try:
            erp.execute(
                """UPDATE cost_center
                   SET name = ?
                   WHERE company_id = ?
                     AND id = (SELECT default_cost_center_id FROM company WHERE id = ?)""",
                (f"{name} — Branch", property_id, property_id),
            )
            erp.commit()
        except Exception:
            pass

        # 2) Chart of Accounts for the new branch
        coa = await execute_skill(
            "erpclaw",
            "setup-chart-of-accounts",
            {"company-id": property_id, "template": tax_template},
            timeout=ONBOARD_TIMEOUT,
            tenant_id=property_id,
            is_superadmin=True,
        )

        # 3) Hospitality schema + seed hotel under this property
        await execute_skill(
            "hospitalityclaw",
            "hospitality-initialize-schema",
            {"tenant-id": property_id},
            timeout=ONBOARD_TIMEOUT,
            tenant_id=property_id,
            is_superadmin=True,
        )
        seed_params = {
            "tenant-id": property_id,
            "hotel-name": name,
            "timezone": timezone_name,
            "currency": currency,
            "country": country,
        }
        if address:
            seed_params["address"] = address
        seed = await execute_skill(
            "hospitalityclaw",
            "hospitality-seed-defaults",
            seed_params,
            timeout=ONBOARD_TIMEOUT,
            tenant_id=property_id,
            is_superadmin=True,
        )

        # 4) Membership so the owner can switch into the new branch
        upsert_membership(
            web,
            ctx.user_id,
            property_id,
            role_code="HOTEL_OWNER",
            role_name="Hotel Owner",
            hotel_name=name,
        )
        # Grant the new branch to existing group-wide staff (Admin / GM / Hotel Accountant).
        from rbac import GROUP_WIDE_ROLES, ROLE_DISPLAY

        sibling_ids = [
            str(p.get("property_id") or "")
            for p in list_properties_in_group(erp, gid)
            if p.get("property_id") and p.get("property_id") != property_id
        ]
        if ctx.tenant_id and ctx.tenant_id not in sibling_ids:
            sibling_ids.append(str(ctx.tenant_id))
        rows = []
        if sibling_ids:
            ph = ",".join("?" for _ in sibling_ids)
            role_ph = ",".join("?" for _ in GROUP_WIDE_ROLES)
            rows = web.execute(
                f"""SELECT DISTINCT user_id, role_code, role_name
                    FROM webclaw_user_tenant
                    WHERE tenant_id IN ({ph})
                      AND role_code IN ({role_ph})
                      AND user_id <> ?""",
                (*sibling_ids, *GROUP_WIDE_ROLES, ctx.user_id),
            ).fetchall()
        seen_users: set[str] = set()
        for row in rows or []:
            uid = row["user_id"] if hasattr(row, "keys") else row[0]
            if not uid or uid in seen_users:
                continue
            seen_users.add(uid)
            rcode = (row["role_code"] if hasattr(row, "keys") else row[1]) or "HOTEL_ADMIN"
            rname = (
                (row["role_name"] if hasattr(row, "keys") else row[2])
                or ROLE_DISPLAY.get(rcode, rcode)
            )
            # Membership only — webclaw_user_role is UNIQUE(user_id, role_id),
            # so group-wide staff keep a single role row and gain branches here.
            upsert_membership(
                web,
                uid,
                property_id,
                role_code=rcode,
                role_name=rname,
                hotel_name=name,
            )
        web.commit()

        # Fresh connection so we see skill subprocess commits (address, etc.).
        try:
            erp.close()
        except Exception:
            pass
        erp = _erp()
        props = list_properties_in_group(erp, gid)
        created = next((p for p in props if p.get("property_id") == property_id), None)

        # Prefer the caller's display name on the branch row (company.name may
        # have been auto-suffixed when the brand name already existed).
        if created is not None:
            created = {**created, "hotel_name": name}
            if address and not created.get("address"):
                created["address"] = address
        try:
            erp.execute(
                "UPDATE pms_tenant SET hotel_name = ? WHERE id = ? OR company_id = ?",
                (name, property_id, property_id),
            )
            erp.commit()
        except Exception:
            pass

        return {
            "status": "ok",
            "group_id": gid,
            "property_id": property_id,
            "tenant_id": property_id,
            "property": created
            or {
                "property_id": property_id,
                "hotel_name": name,
                "timezone": timezone_name,
                "currency": currency,
                "country": country,
                "group_id": gid,
                "address": address,
            },
            "coa": {"status": coa.get("status"), "template": tax_template},
            "hospitality": {"status": seed.get("status") if isinstance(seed, dict) else None},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        try:
            erp.close()
        except Exception:
            pass


@router.patch("/properties/{property_id}/status")
@require_permission("hotel.manage")
async def update_owner_property_status(
    property_id: str,
    request: Request,
    user=Depends(get_current_user),
):
    """Open or close a branch for new bookings (website + OTAs + front desk).

    Body: ``{"status": "active"|"closed"}`` or ``{"accepting_bookings": true|false}``.
    Staff can still work a closed branch; only new reservations are refused and
    OTA availability is pushed as zero.
    """
    ctx = _require_owner_ctx(request)
    try:
        body = await request.json()
    except Exception:
        body = {}

    status_raw = str(body.get("status") or "").strip().lower()
    if "accepting_bookings" in body:
        accepting = str(body.get("accepting_bookings")).lower() not in ("0", "false", "no", "off")
    elif status_raw in ("active", "open", "reopen", "reopened"):
        accepting = True
    elif status_raw in ("closed", "close", "inactive"):
        accepting = False
    else:
        return JSONResponse(
            {
                "status": "error",
                "message": "status must be active or closed (or pass accepting_bookings)",
            },
            status_code=400,
        )

    web = db.get_connection()
    erp = _erp()
    try:
        gid = _ensure_group_for_owner(web, erp, ctx)
        props = list_properties_in_group(erp, gid)
        match = next(
            (
                p
                for p in props
                if p.get("property_id") == property_id or p.get("company_id") == property_id
            ),
            None,
        )
        if not match:
            return JSONResponse(
                {"status": "error", "message": "Property is not in your hotel group"},
                status_code=404,
            )
        try:
            updated = set_accepting_bookings(erp, property_id, accepting)
        except ValueError as exc:
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)

        channel_sync = None
        try:
            from services import channel_manager as cm

            # property_id is the branch tenant/company id used by channel rows.
            channel_sync = cm.push_outbound(
                match.get("property_id") or property_id,
                hotel_id=None,
                reason="branch-closed" if not accepting else "branch-reopened",
            )
        except Exception as exc:
            channel_sync = {"status": "error", "message": str(exc)}

        props = list_properties_in_group(erp, gid)
        property_row = next(
            (p for p in props if p.get("property_id") == updated["property_id"]),
            {**match, **updated},
        )
        return {
            "status": "ok",
            "message": (
                "Branch closed for new bookings"
                if not accepting
                else "Branch reopened for bookings"
            ),
            "property": property_row,
            "channel_sync": channel_sync,
        }
    finally:
        try:
            erp.close()
        except Exception:
            pass
