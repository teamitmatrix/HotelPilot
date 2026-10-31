"""Hotel onboarding pipeline — checklist, invite status, go-live gates.

Platform tables track pipeline state. ERP ``pms_tenant.status`` stays
``active``/``suspended``/``cancelled`` so pause/resume keep working;
analytics uses ``webclaw_tenant_onboarding.status`` instead of the old
14-day heuristic.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

import db
from fastapi import Depends

from auth.deps import get_current_user, require_hotel_tenant
from db_access import DbConn, run_ddl, table_columns, table_exists
from rbac import require_any_permission, require_permission
from tenant import TenantContext

admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin-onboarding"])
hotel_router = APIRouter(prefix="/api/v1/hotel", tags=["hotel-onboarding"])

ONBOARDING_DDL = """
CREATE TABLE IF NOT EXISTS webclaw_tenant_onboarding (
    tenant_id           TEXT PRIMARY KEY,
    hotel_name          TEXT,
    admin_email         TEXT,
    source              TEXT NOT NULL DEFAULT 'admin'
                        CHECK(source IN ('admin','self_serve')),
    status              TEXT NOT NULL DEFAULT 'in_progress'
                        CHECK(status IN ('in_progress','ready','live','abandoned','blocked')),
    owner_invite_status TEXT NOT NULL DEFAULT 'not_needed'
                        CHECK(owner_invite_status IN ('not_needed','pending','accepted','expired')),
    go_live_at          TEXT,
    go_live_by          TEXT,
    notes               TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webclaw_onboarding_step (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    step_key        TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    sort_order      INTEGER NOT NULL DEFAULT 0,
    required        INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','done','skipped','blocked')),
    completed_at    TEXT,
    completed_by    TEXT,
    meta_json       TEXT NOT NULL DEFAULT '{}',
    UNIQUE(tenant_id, step_key)
);
CREATE INDEX IF NOT EXISTS idx_onboarding_step_tenant
    ON webclaw_onboarding_step(tenant_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_onboarding_status
    ON webclaw_tenant_onboarding(status, updated_at DESC);
"""

# Default checklist for a new hotel (required flags drive go-live gate).
DEFAULT_STEPS: list[dict] = [
    {
        "key": "account_created",
        "title": "Account created",
        "description": "Hotel tenant and owner account provisioned",
        "required": True,
        "auto": True,
    },
    {
        "key": "owner_login",
        "title": "Owner signed in",
        "description": "Hotel owner completed at least one login",
        "required": True,
        "auto": True,
    },
    {
        "key": "property_profile",
        "title": "Property profile",
        "description": "Hotel name, timezone, currency confirmed",
        "required": True,
        "auto": True,
    },
    {
        "key": "room_types",
        "title": "Room types",
        "description": "At least one room type configured",
        "required": True,
        "auto": True,
    },
    {
        "key": "rooms",
        "title": "Rooms inventory",
        "description": "At least one sellable room",
        "required": True,
        "auto": True,
    },
    {
        "key": "rates",
        "title": "Rates / rate plan",
        "description": "Base rate or rate plan configured",
        "required": False,
        "auto": True,
    },
    {
        "key": "staff_invited",
        "title": "Staff invited",
        "description": "At least one staff account beyond the owner",
        "required": False,
        "auto": True,
    },
    {
        "key": "go_live_confirm",
        "title": "Go-live confirmation",
        "description": "Operator or owner confirms the hotel is ready",
        "required": True,
        "auto": False,
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row, *keys_or_idx):
    if row is None:
        return None
    if hasattr(row, "keys"):
        for k in keys_or_idx:
            if isinstance(k, str) and k in row.keys():
                return row[k]
        return None
    idx = keys_or_idx[-1] if isinstance(keys_or_idx[-1], int) else 0
    try:
        return row[idx]
    except Exception:
        return None


def ensure_onboarding_tables(conn: DbConn) -> None:
    run_ddl(conn, ONBOARDING_DDL)
    conn.commit()


def _actor(request: Request) -> tuple[str | None, str | None]:
    user = getattr(request.state, "user", None) or {}
    return user.get("user_id") or user.get("id"), user.get("email")


def _audit(conn, request: Request, action: str, detail: str = "", tenant_id=None):
    try:
        from admin_ops import write_platform_audit, _client_ip

        uid, email = _actor(request)
        write_platform_audit(
            conn,
            user_id=uid,
            user_email=email,
            tenant_id=tenant_id,
            action_category="onboarding",
            action=action,
            ip_address=_client_ip(request),
            detail=detail,
        )
    except Exception:
        pass


def start_onboarding(
    conn: DbConn,
    *,
    tenant_id: str,
    hotel_name: str | None = None,
    admin_email: str | None = None,
    source: str = "admin",
    owner_already_logged_in: bool = False,
) -> dict:
    """Create pipeline + steps for a newly provisioned hotel (idempotent)."""
    ensure_onboarding_tables(conn)
    now = _now()
    source = source if source in ("admin", "self_serve") else "admin"
    existing = conn.execute(
        "SELECT tenant_id FROM webclaw_tenant_onboarding WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchone()
    invite = "not_needed" if owner_already_logged_in or source == "self_serve" else "pending"
    if not existing:
        conn.execute(
            """INSERT INTO webclaw_tenant_onboarding
               (tenant_id, hotel_name, admin_email, source, status, owner_invite_status,
                go_live_at, go_live_by, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'in_progress', ?, NULL, NULL, '', ?, ?)""",
            (tenant_id, hotel_name, admin_email, source, invite, now, now),
        )
    else:
        conn.execute(
            """UPDATE webclaw_tenant_onboarding
               SET hotel_name = COALESCE(?, hotel_name),
                   admin_email = COALESCE(?, admin_email),
                   updated_at = ?
               WHERE tenant_id = ? AND status != 'live'""",
            (hotel_name, admin_email, now, tenant_id),
        )

    for i, spec in enumerate(DEFAULT_STEPS):
        exists = conn.execute(
            "SELECT 1 FROM webclaw_onboarding_step WHERE tenant_id = ? AND step_key = ?",
            (tenant_id, spec["key"]),
        ).fetchone()
        if exists:
            continue
        initial = "pending"
        if spec["key"] == "account_created":
            initial = "done"
        elif spec["key"] == "owner_login" and owner_already_logged_in:
            initial = "done"
            invite = "accepted"
        elif spec["key"] == "property_profile" and hotel_name:
            initial = "done"
        conn.execute(
            """INSERT INTO webclaw_onboarding_step
               (id, tenant_id, step_key, title, description, sort_order, required,
                status, completed_at, completed_by, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')""",
            (
                str(uuid.uuid4()),
                tenant_id,
                spec["key"],
                spec["title"],
                spec["description"],
                i,
                1 if spec["required"] else 0,
                initial,
                now if initial == "done" else None,
                "system" if initial == "done" else None,
            ),
        )

    if owner_already_logged_in:
        conn.execute(
            """UPDATE webclaw_tenant_onboarding
               SET owner_invite_status = 'accepted', updated_at = ?
               WHERE tenant_id = ?""",
            (now, tenant_id),
        )
    conn.commit()
    sync_onboarding_progress(conn, tenant_id)
    return get_onboarding_payload(conn, tenant_id)


def _erp_counts(tenant_id: str) -> dict:
    out = {"room_types": 0, "rooms": 0, "rates": 0, "reservations": 0, "hotel_name": None}
    erp = db.get_skill_db("erpclaw")
    if not erp:
        return out
    try:
        if table_exists(erp, "pms_tenant"):
            r = erp.execute(
                "SELECT hotel_name FROM pms_tenant WHERE id = ?", (tenant_id,)
            ).fetchone()
            if r:
                out["hotel_name"] = _row(r, "hotel_name", 0)
        if table_exists(erp, "hospitalityclaw_room_type"):
            out["room_types"] = int(
                erp.execute(
                    "SELECT COUNT(*) FROM hospitalityclaw_room_type WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()[0]
                or 0
            )
        if table_exists(erp, "hospitalityclaw_room"):
            out["rooms"] = int(
                erp.execute(
                    "SELECT COUNT(*) FROM hospitalityclaw_room WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()[0]
                or 0
            )
        if table_exists(erp, "hospitalityclaw_rate_plan"):
            out["rates"] = int(
                erp.execute(
                    "SELECT COUNT(*) FROM hospitalityclaw_rate_plan WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()[0]
                or 0
            )
        if table_exists(erp, "hospitalityclaw_reservation"):
            out["reservations"] = int(
                erp.execute(
                    "SELECT COUNT(*) FROM hospitalityclaw_reservation WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()[0]
                or 0
            )
    except Exception:
        pass
    finally:
        erp.close()
    return out


def _mark_step_done(conn: DbConn, tenant_id: str, step_key: str, by: str = "auto") -> None:
    row = conn.execute(
        """SELECT status FROM webclaw_onboarding_step
           WHERE tenant_id = ? AND step_key = ?""",
        (tenant_id, step_key),
    ).fetchone()
    if not row or _row(row, "status", 0) == "done":
        return
    conn.execute(
        """UPDATE webclaw_onboarding_step
           SET status = 'done', completed_at = ?, completed_by = ?
           WHERE tenant_id = ? AND step_key = ? AND status != 'done'""",
        (_now(), by, tenant_id, step_key),
    )


def sync_onboarding_progress(conn: DbConn, tenant_id: str) -> dict:
    """Auto-complete detectable steps from ERP / user activity."""
    ensure_onboarding_tables(conn)
    pipe = conn.execute(
        "SELECT * FROM webclaw_tenant_onboarding WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    if not pipe:
        return {"status": "missing"}
    if _row(pipe, "status", 4) == "live":
        return get_onboarding_payload(conn, tenant_id)

    counts = _erp_counts(tenant_id)
    admin_email = (_row(pipe, "admin_email", 2) or "").lower()

    # Owner login detection
    cols = table_columns(conn, "webclaw_user") or []
    owner_logged_in = False
    staff_count = 0
    if "tenant_id" in cols:
        if admin_email and "last_login" in cols:
            u = conn.execute(
                """SELECT last_login FROM webclaw_user
                   WHERE tenant_id = ? AND lower(email) = ?""",
                (tenant_id, admin_email),
            ).fetchone()
            if u and _row(u, "last_login", 0):
                owner_logged_in = True
        staff_count = int(
            conn.execute(
                """SELECT COUNT(*) FROM webclaw_user
                   WHERE tenant_id = ? AND status != 'deleted'""",
                (tenant_id,),
            ).fetchone()[0]
            or 0
        )

    _mark_step_done(conn, tenant_id, "account_created")
    if counts.get("hotel_name"):
        _mark_step_done(conn, tenant_id, "property_profile")
    if counts["room_types"] > 0:
        _mark_step_done(conn, tenant_id, "room_types")
    if counts["rooms"] > 0:
        _mark_step_done(conn, tenant_id, "rooms")
    if counts["rates"] > 0:
        _mark_step_done(conn, tenant_id, "rates")
    if staff_count > 1:
        _mark_step_done(conn, tenant_id, "staff_invited")
    if owner_logged_in:
        _mark_step_done(conn, tenant_id, "owner_login")
        conn.execute(
            """UPDATE webclaw_tenant_onboarding
               SET owner_invite_status = 'accepted', updated_at = ?
               WHERE tenant_id = ? AND owner_invite_status != 'accepted'""",
            (_now(), tenant_id),
        )

    # Refresh ready vs in_progress (ready = all gates except go_live_confirm)
    payload = get_onboarding_payload(conn, tenant_id, refresh=False)
    if payload.get("status") != "live":
        new_status = "ready" if payload.get("can_go_live") else "in_progress"
        conn.execute(
            """UPDATE webclaw_tenant_onboarding
               SET status = ?, hotel_name = COALESCE(?, hotel_name), updated_at = ?
               WHERE tenant_id = ? AND status NOT IN ('live','abandoned')""",
            (new_status, counts.get("hotel_name"), _now(), tenant_id),
        )
        conn.commit()
    else:
        conn.commit()
    return get_onboarding_payload(conn, tenant_id)


def get_onboarding_payload(conn: DbConn, tenant_id: str, *, refresh: bool = True) -> dict:
    ensure_onboarding_tables(conn)
    if refresh:
        # Avoid infinite recursion: sync calls us with refresh=False
        pipe_check = conn.execute(
            "SELECT status FROM webclaw_tenant_onboarding WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if pipe_check and _row(pipe_check, "status", 0) != "live":
            # lightweight — caller may already be syncing
            pass

    pipe = conn.execute(
        "SELECT * FROM webclaw_tenant_onboarding WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    if not pipe:
        return {
            "tenant_id": tenant_id,
            "status": "missing",
            "source": None,
            "owner_invite_status": None,
            "required_complete": False,
            "can_go_live": False,
            "steps": [],
            "progress": {"done": 0, "total": 0, "required_done": 0, "required_total": 0},
        }

    steps_rows = conn.execute(
        """SELECT step_key, title, description, sort_order, required, status,
                  completed_at, completed_by
           FROM webclaw_onboarding_step
           WHERE tenant_id = ?
           ORDER BY sort_order, step_key""",
        (tenant_id,),
    ).fetchall()
    steps = []
    done = 0
    req_done = 0
    req_total = 0
    for r in steps_rows:
        required = bool(_row(r, "required", 4))
        status = _row(r, "status", 5)
        if required:
            req_total += 1
            if status == "done":
                req_done += 1
        if status in ("done", "skipped"):
            done += 1
        steps.append(
            {
                "key": _row(r, "step_key", 0),
                "title": _row(r, "title", 1),
                "description": _row(r, "description", 2),
                "sort_order": _row(r, "sort_order", 3),
                "required": required,
                "status": status,
                "completed_at": _row(r, "completed_at", 6),
                "completed_by": _row(r, "completed_by", 7),
            }
        )

    pipe_status = _row(pipe, "status", 4)
    required_complete = req_total > 0 and req_done >= req_total
    # Go-live allowed when every required step except explicit confirm is done
    gates_ok = all(
        s["status"] == "done"
        for s in steps
        if s["required"] and s["key"] != "go_live_confirm"
    )
    can_go_live = gates_ok and pipe_status != "live"
    return {
        "tenant_id": tenant_id,
        "hotel_name": _row(pipe, "hotel_name", 1),
        "admin_email": _row(pipe, "admin_email", 2),
        "source": _row(pipe, "source", 3),
        "status": pipe_status,
        "owner_invite_status": _row(pipe, "owner_invite_status", 5),
        "go_live_at": _row(pipe, "go_live_at", 6),
        "go_live_by": _row(pipe, "go_live_by", 7),
        "notes": _row(pipe, "notes", 8) or "",
        "created_at": _row(pipe, "created_at", 9),
        "updated_at": _row(pipe, "updated_at", 10),
        "required_complete": required_complete,
        "can_go_live": can_go_live,
        "steps": steps,
        "progress": {
            "done": done,
            "total": len(steps),
            "required_done": req_done,
            "required_total": req_total,
        },
    }


def count_onboarding_in_progress(conn: DbConn) -> int:
    ensure_onboarding_tables(conn)
    row = conn.execute(
        """SELECT COUNT(*) FROM webclaw_tenant_onboarding
           WHERE status IN ('in_progress','ready','blocked')"""
    ).fetchone()
    return int(row[0] if row else 0)


def go_live(conn: DbConn, tenant_id: str, *, actor_email: str | None = None) -> dict:
    ensure_onboarding_tables(conn)
    payload = sync_onboarding_progress(conn, tenant_id)
    if payload.get("status") == "missing":
        return {"ok": False, "error": "Onboarding not started", "http_status": 404}
    if payload.get("status") == "live":
        return {"ok": True, "onboarding": payload}
    if not payload.get("can_go_live"):
        pending = [
            s["key"]
            for s in payload.get("steps", [])
            if s.get("required")
            and s.get("key") != "go_live_confirm"
            and s.get("status") != "done"
        ]
        return {
            "ok": False,
            "error": f"Required steps incomplete: {', '.join(pending) or 'unknown'}",
            "http_status": 400,
            "pending": pending,
        }
    now = _now()
    _mark_step_done(conn, tenant_id, "go_live_confirm", by=actor_email or "operator")
    conn.execute(
        """UPDATE webclaw_tenant_onboarding
           SET status = 'live', go_live_at = ?, go_live_by = ?, updated_at = ?
           WHERE tenant_id = ?""",
        (now, actor_email, now, tenant_id),
    )
    conn.commit()
    try:
        from admin_platform import _record_subscription_event, _upsert_lifecycle

        _record_subscription_event(
            conn,
            tenant_id,
            "go_live",
            f"Hotel went live ({actor_email or 'system'})",
            actor_email,
        )
        _upsert_lifecycle(conn, tenant_id, "active")
    except Exception:
        pass
    return {"ok": True, "onboarding": get_onboarding_payload(conn, tenant_id)}


class StepUpdate(BaseModel):
    status: str
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("status")
    @classmethod
    def status_ok(cls, v: str) -> str:
        value = v.lower()
        if value not in {"pending", "done", "skipped", "blocked"}:
            raise ValueError("status must be pending, done, skipped, or blocked")
        return value


# ── Admin routes ──────────────────────────────────────────────────────────────

@admin_router.get("/onboarding")
@require_any_permission("platform.tenants.view", "platform.tenants.manage", "platform.admin")
async def list_onboarding_pipelines(request: Request, status: str | None = None):
    conn = db.get_connection()
    ensure_onboarding_tables(conn)
    if status and status != "all":
        rows = conn.execute(
            """SELECT tenant_id, hotel_name, admin_email, source, status,
                      owner_invite_status, go_live_at, created_at, updated_at
               FROM webclaw_tenant_onboarding WHERE status = ?
               ORDER BY updated_at DESC LIMIT 200""",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT tenant_id, hotel_name, admin_email, source, status,
                      owner_invite_status, go_live_at, created_at, updated_at
               FROM webclaw_tenant_onboarding
               ORDER BY updated_at DESC LIMIT 200"""
        ).fetchall()
    items = []
    for r in rows:
        tid = str(_row(r, "tenant_id", 0))
        payload = sync_onboarding_progress(conn, tid)
        items.append(
            {
                "tenant_id": tid,
                "hotel_name": _row(r, "hotel_name", 1) or payload.get("hotel_name"),
                "admin_email": _row(r, "admin_email", 2),
                "source": _row(r, "source", 3),
                "status": payload.get("status") or _row(r, "status", 4),
                "owner_invite_status": payload.get("owner_invite_status"),
                "go_live_at": _row(r, "go_live_at", 6),
                "progress": payload.get("progress"),
                "can_go_live": payload.get("can_go_live"),
                "created_at": _row(r, "created_at", 7),
                "updated_at": _row(r, "updated_at", 8),
            }
        )
    return {"status": "ok", "pipelines": items, "in_progress_count": count_onboarding_in_progress(conn)}


@admin_router.get("/tenants/{tenant_id}/onboarding")
@require_any_permission("platform.tenants.view", "platform.tenants.manage", "platform.admin")
async def get_tenant_onboarding(tenant_id: str, request: Request):
    conn = db.get_connection()
    ensure_onboarding_tables(conn)
    pipe = conn.execute(
        "SELECT 1 FROM webclaw_tenant_onboarding WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    if not pipe:
        # Backfill for hotels created before pipeline existed
        start_onboarding(conn, tenant_id=tenant_id, source="admin")
    return {"status": "ok", "onboarding": sync_onboarding_progress(conn, tenant_id)}


@admin_router.patch("/tenants/{tenant_id}/onboarding/steps/{step_key}")
@require_any_permission("platform.tenants.manage", "platform.admin")
async def update_onboarding_step(tenant_id: str, step_key: str, request: Request):
    try:
        body = await request.json()
        req = StepUpdate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_onboarding_tables(conn)
    row = conn.execute(
        "SELECT id FROM webclaw_onboarding_step WHERE tenant_id = ? AND step_key = ?",
        (tenant_id, step_key),
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "Step not found"}, 404)
    uid, email = _actor(request)
    now = _now()
    completed_at = now if req.status in ("done", "skipped") else None
    completed_by = email or uid if completed_at else None
    conn.execute(
        """UPDATE webclaw_onboarding_step
           SET status = ?, completed_at = ?, completed_by = ?
           WHERE tenant_id = ? AND step_key = ?""",
        (req.status, completed_at, completed_by, tenant_id, step_key),
    )
    if req.notes:
        conn.execute(
            """UPDATE webclaw_tenant_onboarding SET notes = ?, updated_at = ?
               WHERE tenant_id = ?""",
            (req.notes, now, tenant_id),
        )
    conn.commit()
    _audit(conn, request, "step_update", f"{step_key}={req.status}", tenant_id)
    return {"status": "ok", "onboarding": sync_onboarding_progress(conn, tenant_id)}


@admin_router.post("/tenants/{tenant_id}/onboarding/go-live")
@require_any_permission("platform.tenants.manage", "platform.admin")
async def admin_go_live(tenant_id: str, request: Request):
    conn = db.get_connection()
    _, email = _actor(request)
    # Allow operator override: mark go_live_confirm done first if all else done
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    force = bool(body.get("force"))
    if force:
        ensure_onboarding_tables(conn)
        payload = sync_onboarding_progress(conn, tenant_id)
        for s in payload.get("steps", []):
            if s.get("required") and s.get("key") != "go_live_confirm" and s.get("status") != "done":
                return JSONResponse(
                    {
                        "status": "error",
                        "message": f"Cannot force go-live; still missing {s['key']}",
                    },
                    400,
                )
        _mark_step_done(conn, tenant_id, "go_live_confirm", by=email or "operator")
        conn.commit()
    result = go_live(conn, tenant_id, actor_email=email)
    if not result.get("ok"):
        return JSONResponse(
            {"status": "error", "message": result.get("error"), "pending": result.get("pending")},
            result.get("http_status", 400),
        )
    _audit(conn, request, "go_live", "Hotel marked live", tenant_id)
    return {"status": "ok", "onboarding": result["onboarding"]}


@admin_router.post("/tenants/{tenant_id}/onboarding/start")
@require_any_permission("platform.tenants.manage", "platform.admin")
async def admin_start_onboarding(tenant_id: str, request: Request):
    """Backfill / restart pipeline for an existing hotel (does not un-live)."""
    conn = db.get_connection()
    hotel_name = None
    admin_email = None
    erp = db.get_skill_db("erpclaw")
    if erp:
        try:
            r = erp.execute(
                "SELECT hotel_name, admin_email FROM pms_tenant WHERE id = ?",
                (tenant_id,),
            ).fetchone()
            if r:
                hotel_name = _row(r, "hotel_name", 0)
                admin_email = _row(r, "admin_email", 1)
        finally:
            erp.close()
    if not hotel_name:
        return JSONResponse({"status": "error", "message": "Hotel not found"}, 404)
    existing = conn.execute(
        "SELECT status FROM webclaw_tenant_onboarding WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchone()
    if existing and _row(existing, "status", 0) == "live":
        return {"status": "ok", "onboarding": get_onboarding_payload(conn, tenant_id)}
    payload = start_onboarding(
        conn,
        tenant_id=tenant_id,
        hotel_name=hotel_name,
        admin_email=admin_email,
        source="admin",
    )
    _audit(conn, request, "start", "Pipeline started/backfilled", tenant_id)
    return {"status": "ok", "onboarding": payload}


# ── Hotel-facing routes ───────────────────────────────────────────────────────

@hotel_router.get("/onboarding")
@require_permission("hotel.manage")
async def hotel_get_onboarding(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    ctx: TenantContext = Depends(get_current_user),
):
    conn = db.get_connection()
    ensure_onboarding_tables(conn)
    tid = tenant_id
    email = getattr(ctx, "email", None) or ""
    if not conn.execute(
        "SELECT 1 FROM webclaw_tenant_onboarding WHERE tenant_id = ?", (tid,)
    ).fetchone():
        start_onboarding(
            conn,
            tenant_id=tid,
            hotel_name=getattr(ctx, "hotel_name", None),
            admin_email=email,
            source="self_serve",
            owner_already_logged_in=True,
        )
    _mark_step_done(conn, tid, "owner_login", by=email or "owner")
    conn.execute(
        """UPDATE webclaw_tenant_onboarding
           SET owner_invite_status = 'accepted', updated_at = ?
           WHERE tenant_id = ?""",
        (_now(), tid),
    )
    conn.commit()
    return {"status": "ok", "onboarding": sync_onboarding_progress(conn, tid)}


@hotel_router.post("/onboarding/steps/{step_key}/complete")
@require_permission("hotel.manage")
async def hotel_complete_step(
    step_key: str,
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    ctx: TenantContext = Depends(get_current_user),
):
    conn = db.get_connection()
    ensure_onboarding_tables(conn)
    tid = tenant_id
    row = conn.execute(
        "SELECT id FROM webclaw_onboarding_step WHERE tenant_id = ? AND step_key = ?",
        (tid, step_key),
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "Step not found"}, 404)
    email = getattr(ctx, "email", None) or "owner"
    _mark_step_done(conn, tid, step_key, by=email)
    conn.commit()
    return {"status": "ok", "onboarding": sync_onboarding_progress(conn, tid)}


@hotel_router.post("/onboarding/go-live")
@require_permission("hotel.manage")
async def hotel_go_live(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    ctx: TenantContext = Depends(get_current_user),
):
    conn = db.get_connection()
    tid = tenant_id
    email = getattr(ctx, "email", None)
    _mark_step_done(conn, tid, "go_live_confirm", by=email or "owner")
    conn.commit()
    result = go_live(conn, tid, actor_email=email)
    if not result.get("ok"):
        return JSONResponse(
            {"status": "error", "message": result.get("error"), "pending": result.get("pending")},
            result.get("http_status", 400),
        )
    return {"status": "ok", "onboarding": result["onboarding"]}
