"""Hotel-scoped staff user management.

Hotel Owner / Admin / General Manager / Branch Manager
(``hotel.staff.manage``) can invite and manage accounts within the roles
they are allowed to assign. Platform SuperAdmin users and cross-tenant
accounts are never listed or mutated here.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.requests import ClientDisconnect

import db
from auth.deps import get_current_user, require_hotel_tenant
from auth.passwords import hash_password, validate_password_strength
from rbac import (
    GROUP_WIDE_ROLES,
    HOTEL_OWNER,
    HOTEL_STAFF_ASSIGNABLE_ROLES,
    HOTEL_STAFF_ROLES,
    ROLE_DISPLAY,
    assignable_roles_for_actor,
    require_permission,
    seed_rbac,
)
from tenant import TenantContext, upsert_membership


async def _read_json(request: Request) -> dict:
    """Read JSON body first so slow work never races a client disconnect."""
    try:
        body = await request.json()
    except ClientDisconnect:
        raise
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}

router = APIRouter(prefix="/api/v1/hotel", tags=["hotel-users"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_STATUS_ACTIVE = "active"
_STATUS_DISABLED = "disabled"

_ROLE_PRIORITY = {
    HOTEL_OWNER: 0,
    "HOTEL_ADMIN": 1,
    "GENERAL_MANAGER": 2,
    "HOTEL_MANAGER": 3,
    "HOTEL_ACCOUNTANT": 4,
    "FRONT_DESK": 5,
    "HOUSEKEEPING": 6,
    "ACCOUNTANT": 7,
}


def _platform_connection():
    """Provide a request-scoped platform connection."""
    conn = db.get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _role_row(conn, code: str):
    row = conn.execute(
        "SELECT id, code, name, description FROM webclaw_role WHERE code = ?",
        (code,),
    ).fetchone()
    if not row:
        seed_rbac(conn)
        row = conn.execute(
            "SELECT id, code, name, description FROM webclaw_role WHERE code = ?",
            (code,),
        ).fetchone()
    return row


def _row_dict(row) -> dict:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(row)


def _user_belongs_to_tenant(conn, user_id: str, tenant_id: str) -> bool:
    home = conn.execute(
        "SELECT tenant_id FROM webclaw_user WHERE id = ?", (user_id,)
    ).fetchone()
    if home and (home["tenant_id"] if hasattr(home, "keys") else home[0]) == tenant_id:
        return True
    mem = conn.execute(
        "SELECT 1 FROM webclaw_user_tenant WHERE user_id = ? AND tenant_id = ? LIMIT 1",
        (user_id, tenant_id),
    ).fetchone()
    return mem is not None


def _staff_role_for_user(conn, user_id: str, tenant_id: str) -> dict | None:
    """Primary hotel role for this tenant (prefer assignable / owner codes)."""
    case_bits = " ".join(
        f"WHEN '{code}' THEN {prio}" for code, prio in _ROLE_PRIORITY.items()
    )
    rows = conn.execute(
        f"""SELECT r.id, r.code, r.name
           FROM webclaw_user_role ur
           JOIN webclaw_role r ON r.id = ur.role_id
           WHERE ur.user_id = ?
             AND (ur.tenant_id IS NULL OR ur.tenant_id = ?)
           ORDER BY CASE r.code
             {case_bits}
             ELSE 9 END""",
        (user_id, tenant_id),
    ).fetchall()
    if not rows:
        mem = conn.execute(
            """SELECT role_code, role_name FROM webclaw_user_tenant
               WHERE user_id = ? AND tenant_id = ?""",
            (user_id, tenant_id),
        ).fetchone()
        if mem:
            code = mem["role_code"] if hasattr(mem, "keys") else mem[0]
            name = mem["role_name"] if hasattr(mem, "keys") else mem[1]
            if code:
                return {"id": None, "code": code, "name": name or ROLE_DISPLAY.get(code, code)}
        return None
    row = rows[0]
    return {
        "id": row["id"] if hasattr(row, "keys") else row[0],
        "code": row["code"] if hasattr(row, "keys") else row[1],
        "name": row["name"] if hasattr(row, "keys") else row[2],
    }


def _actor_role_code(conn, ctx: TenantContext, tenant_id: str) -> str | None:
    role = _staff_role_for_user(conn, ctx.user_id, tenant_id)
    if role and role.get("code"):
        return str(role["code"])
    from tenant import role_code_from_name

    for raw in getattr(ctx, "roles", ()) or ():
        mapped = role_code_from_name(str(raw))
        if mapped:
            return mapped
    ctx_role = getattr(ctx, "role", None)
    return role_code_from_name(str(ctx_role)) if ctx_role else None


def _serialize_user(conn, row, tenant_id: str) -> dict:
    u = _row_dict(row)
    role = _staff_role_for_user(conn, u["id"], tenant_id)
    status = u.get("status") or _STATUS_ACTIVE
    return {
        "id": u["id"],
        "email": u.get("email"),
        "full_name": u.get("full_name") or u.get("username") or "",
        "username": u.get("username"),
        "status": status,
        "status_label": "Active" if status == _STATUS_ACTIVE else "Suspended",
        "last_login": u.get("last_login"),
        "created_at": u.get("created_at"),
        "role": role,
        "role_code": role["code"] if role else None,
        "role_name": role["name"] if role else None,
        "is_owner": bool(role and role.get("code") == HOTEL_OWNER),
        "group_wide": bool(role and role.get("code") in GROUP_WIDE_ROLES),
    }


def _hotel_name(conn, tenant_id: str) -> str | None:
    try:
        row = conn.execute(
            "SELECT hotel_name FROM webclaw_user_tenant WHERE tenant_id = ? AND hotel_name IS NOT NULL LIMIT 1",
            (tenant_id,),
        ).fetchone()
        if row:
            return row["hotel_name"] if hasattr(row, "keys") else row[0]
    except Exception:
        pass
    return None


def _group_property_ids(conn, tenant_id: str, actor_user_id: str | None = None) -> list[str]:
    """Property IDs in the hotel group — webclaw only (no ERP round-trip).

    Uses the actor's memberships / group_id so staff updates never hang on ERP.
    """
    ids: set[str] = {str(tenant_id)}
    if actor_user_id:
        try:
            rows = conn.execute(
                "SELECT tenant_id FROM webclaw_user_tenant WHERE user_id = ?",
                (actor_user_id,),
            ).fetchall()
            for row in rows:
                tid = row["tenant_id"] if hasattr(row, "keys") else row[0]
                if tid:
                    ids.add(str(tid))
        except Exception:
            pass
        try:
            grow = conn.execute(
                "SELECT group_id FROM webclaw_user WHERE id = ?",
                (actor_user_id,),
            ).fetchone()
            gid = grow["group_id"] if grow is not None and hasattr(grow, "keys") else (
                grow[0] if grow else None
            )
            if gid:
                rows = conn.execute(
                    """SELECT DISTINCT ut.tenant_id
                       FROM webclaw_user_tenant ut
                       JOIN webclaw_user u ON u.id = ut.user_id
                       WHERE u.group_id = ?""",
                    (gid,),
                ).fetchall()
                for row in rows:
                    tid = row["tenant_id"] if hasattr(row, "keys") else row[0]
                    if tid:
                        ids.add(str(tid))
        except Exception:
            pass
    return list(ids)


def _sync_memberships(
    conn,
    user_id: str,
    tenant_id: str,
    role_code: str,
    role_name: str,
    *,
    property_ids: list[str] | None = None,
) -> None:
    """Bind memberships: group-wide roles to every known branch; others to one property."""
    hotel = _hotel_name(conn, tenant_id)
    if role_code in GROUP_WIDE_ROLES:
        targets = property_ids or [tenant_id]
        for prop_id in targets:
            upsert_membership(
                conn,
                user_id,
                prop_id,
                role_code=role_code,
                role_name=role_name,
                hotel_name=_hotel_name(conn, prop_id) or hotel,
            )
    else:
        upsert_membership(
            conn,
            user_id,
            tenant_id,
            role_code=role_code,
            role_name=role_name,
            hotel_name=hotel,
        )


def _set_user_role(
    conn,
    user_id: str,
    tenant_id: str,
    role_code: str,
    *,
    actor_user_id: str | None = None,
) -> dict:
    if role_code not in HOTEL_STAFF_ASSIGNABLE_ROLES and role_code != HOTEL_OWNER:
        raise HTTPException(
            status_code=400,
            detail=f"Role must be one of: {', '.join(HOTEL_STAFF_ASSIGNABLE_ROLES)}",
        )
    role = _role_row(conn, role_code)
    if not role:
        raise HTTPException(status_code=400, detail=f"Unknown role: {role_code}")
    role_id = role["id"] if hasattr(role, "keys") else role[0]
    role_name = role["name"] if hasattr(role, "keys") else role[2]

    staff_codes = (*HOTEL_STAFF_ROLES, HOTEL_OWNER)
    placeholders = ",".join("?" for _ in staff_codes)
    group_tenants = _group_property_ids(conn, tenant_id, actor_user_id)

    # Schema UNIQUE(user_id, role_id) — one role row per user/role.
    # Multi-branch access for group-wide roles is via webclaw_user_tenant.
    conn.execute(
        f"""DELETE FROM webclaw_user_role
            WHERE user_id = ?
              AND role_id IN (SELECT id FROM webclaw_role WHERE code IN ({placeholders}))""",
        (user_id, *staff_codes),
    )

    # Drop stale memberships when demoting from / switching group-wide roles.
    try:
        if role_code in GROUP_WIDE_ROLES:
            if group_tenants:
                ph = ",".join("?" for _ in group_tenants)
                conn.execute(
                    f"""DELETE FROM webclaw_user_tenant
                        WHERE user_id = ?
                          AND tenant_id NOT IN ({ph})""",
                    (user_id, *group_tenants),
                )
        else:
            conn.execute(
                "DELETE FROM webclaw_user_tenant WHERE user_id = ? AND tenant_id <> ?",
                (user_id, tenant_id),
            )
    except Exception:
        pass

    bind_tenants = group_tenants if role_code in GROUP_WIDE_ROLES else [tenant_id]
    conn.execute(
        """INSERT INTO webclaw_user_role (id, user_id, role_id, tenant_id)
           VALUES (?, ?, ?, ?)""",
        (str(uuid.uuid4()), user_id, role_id, tenant_id),
    )
    _sync_memberships(
        conn,
        user_id,
        tenant_id,
        role_code,
        role_name,
        property_ids=bind_tenants,
    )
    return {"id": role_id, "code": role_code, "name": role_name}


def _assert_not_last_owner(conn, tenant_id: str, user_id: str) -> None:
    owners = conn.execute(
        """SELECT DISTINCT u.id
           FROM webclaw_user u
           JOIN webclaw_user_role ur ON ur.user_id = u.id
           JOIN webclaw_role r ON r.id = ur.role_id
           WHERE r.code = ?
             AND u.status = ?
             AND (
               u.tenant_id = ?
               OR EXISTS (
                 SELECT 1 FROM webclaw_user_tenant ut
                 WHERE ut.user_id = u.id AND ut.tenant_id = ?
               )
             )
             AND (ur.tenant_id IS NULL OR ur.tenant_id = ?)""",
        (HOTEL_OWNER, _STATUS_ACTIVE, tenant_id, tenant_id, tenant_id),
    ).fetchall()
    owner_ids = {(o["id"] if hasattr(o, "keys") else o[0]) for o in owners}
    if user_id in owner_ids and len(owner_ids) <= 1:
        raise HTTPException(
            status_code=400,
            detail="Cannot revoke or demote the last Hotel Owner for this property",
        )


def _assert_can_assign(actor_code: str | None, role_code: str) -> tuple[str, ...]:
    allowed = assignable_roles_for_actor(actor_code)
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="Your role cannot manage hotel staff",
        )
    if role_code not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Your role cannot assign {role_code}. Allowed: {', '.join(allowed)}",
        )
    return allowed


def _assert_can_mutate_target(
    actor_code: str | None,
    target_code: str | None,
) -> None:
    """Managers/GM may not edit peers or higher roles outside their assignable set."""
    if not target_code or target_code == HOTEL_OWNER:
        # Owner mutations still go through last-owner guard separately.
        if target_code == HOTEL_OWNER and actor_code not in (HOTEL_OWNER, "HOTEL_ADMIN"):
            raise HTTPException(status_code=403, detail="Cannot modify the Hotel Owner")
        return
    allowed = assignable_roles_for_actor(actor_code)
    # Actor may edit targets whose role they could assign (or themselves via other rules).
    if target_code not in allowed and target_code != actor_code:
        # Owner/Admin may edit any staff including other admins/GM.
        if actor_code in (HOTEL_OWNER, "HOTEL_ADMIN"):
            return
        raise HTTPException(
            status_code=403,
            detail=f"Your role cannot manage users with role {target_code}",
        )


@router.get("/users")
@require_permission("hotel.staff.manage")
async def list_hotel_users(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    ctx: TenantContext = Depends(get_current_user),
    conn=Depends(_platform_connection),
):
    """List staff accounts belonging to the active hotel tenant."""
    seed_rbac(conn)
    actor_code = _actor_role_code(conn, ctx, tenant_id)
    allowed = assignable_roles_for_actor(actor_code)
    rows = conn.execute(
        """SELECT DISTINCT u.id, u.username, u.email, u.full_name, u.status,
                  u.last_login, u.created_at, u.tenant_id,
                  LOWER(COALESCE(u.full_name, u.email)) AS sort_name
           FROM webclaw_user u
           WHERE u.tenant_id = ?
              OR EXISTS (
                SELECT 1 FROM webclaw_user_tenant ut
                WHERE ut.user_id = u.id AND ut.tenant_id = ?
              )
           ORDER BY sort_name""",
        (tenant_id, tenant_id),
    ).fetchall()
    users = [_serialize_user(conn, r, tenant_id) for r in rows]
    # Never expose platform SuperAdmins in hotel staff lists.
    users = [u for u in users if u.get("role_code") != "SUPER_ADMIN"]
    return {
        "status": "ok",
        "users": users,
        "assignable_roles": [
            {"code": code, "name": ROLE_DISPLAY[code]}
            for code in allowed
        ],
        "actor_role": actor_code,
        "tenant_id": tenant_id,
    }


@router.post("/users")
@require_permission("hotel.staff.manage")
async def create_hotel_user(
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    ctx: TenantContext = Depends(get_current_user),
    conn=Depends(_platform_connection),
):
    """Create a staff account bound to the admin's active tenant (or all branches)."""
    try:
        body = await _read_json(request)
    except ClientDisconnect:
        return Response(status_code=499)
    email = (body.get("email") or "").strip().lower()
    full_name = (body.get("full_name") or body.get("name") or "").strip()
    password = body.get("password") or body.get("temporary_password") or ""
    role_code = (body.get("role") or body.get("role_code") or "").strip().upper()

    if not full_name:
        return JSONResponse({"status": "error", "message": "Name is required"}, 400)
    if not email or not _EMAIL_RE.match(email):
        return JSONResponse({"status": "error", "message": "Valid email is required"}, 400)

    seed_rbac(conn)
    actor_code = _actor_role_code(conn, ctx, tenant_id)
    try:
        _assert_can_assign(actor_code, role_code)
    except HTTPException as exc:
        return JSONResponse({"status": "error", "message": exc.detail}, exc.status_code)

    pw_err = validate_password_strength(password)
    if pw_err:
        return JSONResponse({"status": "error", "message": pw_err}, 400)

    existing = conn.execute(
        "SELECT id, tenant_id FROM webclaw_user WHERE email = ?", (email,)
    ).fetchone()
    if existing:
        return JSONResponse(
            {"status": "error", "message": "Email already registered"},
            409,
        )

    try:
        from admin_growth import assert_within_plan_limits

        limit_err = assert_within_plan_limits(str(tenant_id), "staff", delta=1)
        if limit_err:
            return JSONResponse(limit_err, status_code=403)
    except Exception:
        pass

    user_id = str(uuid.uuid4())
    username = email.split("@")[0]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO webclaw_user
           (id, username, email, full_name, password_hash, status, created_at, tenant_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            username,
            email,
            full_name,
            hash_password(password),
            _STATUS_ACTIVE,
            now,
            tenant_id,
        ),
    )
    # Propagate group_id for group-wide roles when present on the actor.
    try:
        if role_code in GROUP_WIDE_ROLES and getattr(ctx, "group_id", None):
            conn.execute(
                "UPDATE webclaw_user SET group_id = ? WHERE id = ?",
                (ctx.group_id, user_id),
            )
    except Exception:
        pass

    role = _set_user_role(
        conn, user_id, tenant_id, role_code, actor_user_id=ctx.user_id
    )
    conn.commit()

    row = conn.execute("SELECT * FROM webclaw_user WHERE id = ?", (user_id,)).fetchone()
    return {
        "status": "ok",
        "user": _serialize_user(conn, row, tenant_id),
        "role": role,
        "message": "Staff member created",
    }


@router.patch("/users/{user_id}")
@require_permission("hotel.staff.manage")
async def update_hotel_user(
    user_id: str,
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    ctx: TenantContext = Depends(get_current_user),
    conn=Depends(_platform_connection),
):
    """Update staff details, role, password, or active/suspended status."""
    # Read body before any slow work (seed_rbac / ERP) so reload/timeouts
    # don't raise ClientDisconnect mid-handler.
    try:
        body = await _read_json(request)
    except ClientDisconnect:
        return Response(status_code=499)

    full_name = body.get("full_name") or body.get("name")
    email_raw = body.get("email")
    status = body.get("status")
    if status == "suspended":
        status = _STATUS_DISABLED
    if status == "inactive":
        status = _STATUS_DISABLED
    role_code = body.get("role") or body.get("role_code")
    if role_code:
        role_code = str(role_code).strip().upper()
    new_password = body.get("password") or body.get("temporary_password")

    try:
        seed_rbac(conn)
        if not _user_belongs_to_tenant(conn, user_id, tenant_id):
            raise HTTPException(status_code=404, detail="Staff member not found")

        row = conn.execute("SELECT * FROM webclaw_user WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Staff member not found")

        actor_code = _actor_role_code(conn, ctx, tenant_id)
        current_role = _staff_role_for_user(conn, user_id, tenant_id)
        if current_role and current_role.get("code") == "SUPER_ADMIN":
            raise HTTPException(status_code=403, detail="Cannot modify platform administrators")

        target_code = current_role.get("code") if current_role else None
        _assert_can_mutate_target(actor_code, target_code)

        if status == _STATUS_DISABLED or (role_code and role_code != HOTEL_OWNER):
            if current_role and current_role.get("code") == HOTEL_OWNER:
                _assert_not_last_owner(conn, tenant_id, user_id)

        if user_id == ctx.user_id and status == _STATUS_DISABLED:
            raise HTTPException(status_code=400, detail="You cannot suspend your own account")

        if full_name is not None:
            name = str(full_name).strip()
            if not name:
                return JSONResponse({"status": "error", "message": "Name is required"}, 400)
            conn.execute("UPDATE webclaw_user SET full_name = ? WHERE id = ?", (name, user_id))

        if email_raw is not None:
            email = str(email_raw).strip().lower()
            if not email or not _EMAIL_RE.match(email):
                return JSONResponse(
                    {"status": "error", "message": "Valid email is required"},
                    400,
                )
            current_email = None
            if hasattr(row, "keys"):
                current_email = (row["email"] or "").strip().lower()
            else:
                # fallback if row is positional — prefer keys via serialize
                current_email = str(_row_dict(row).get("email") or "").strip().lower()
            if email != current_email:
                taken = conn.execute(
                    "SELECT id FROM webclaw_user WHERE email = ? AND id <> ?",
                    (email, user_id),
                ).fetchone()
                if taken:
                    return JSONResponse(
                        {"status": "error", "message": "Email already registered"},
                        409,
                    )
                conn.execute(
                    "UPDATE webclaw_user SET email = ? WHERE id = ?",
                    (email, user_id),
                )

        if status in (_STATUS_ACTIVE, _STATUS_DISABLED):
            conn.execute("UPDATE webclaw_user SET status = ? WHERE id = ?", (status, user_id))

        if role_code and role_code != (current_role.get("code") if current_role else None):
            _assert_can_assign(actor_code, role_code)
            if current_role and current_role.get("code") == HOTEL_OWNER and role_code != HOTEL_OWNER:
                _assert_not_last_owner(conn, tenant_id, user_id)
            _set_user_role(
                conn,
                user_id,
                tenant_id,
                role_code,
                actor_user_id=ctx.user_id,
            )

        if new_password:
            pw_err = validate_password_strength(new_password)
            if pw_err:
                return JSONResponse({"status": "error", "message": pw_err}, 400)
            conn.execute(
                "UPDATE webclaw_user SET password_hash = ? WHERE id = ?",
                (hash_password(new_password), user_id),
            )
            try:
                conn.execute("DELETE FROM webclaw_session WHERE user_id = ?", (user_id,))
            except Exception:
                pass

        conn.commit()
        updated = conn.execute("SELECT * FROM webclaw_user WHERE id = ?", (user_id,)).fetchone()
        return {"status": "ok", "user": _serialize_user(conn, updated, tenant_id)}
    except HTTPException:
        raise
    except ClientDisconnect:
        return Response(status_code=499)
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return JSONResponse(
            {"status": "error", "message": f"Could not update staff: {exc}"},
            status_code=500,
        )


@router.delete("/users/{user_id}")
@require_permission("hotel.staff.manage")
async def revoke_hotel_user(
    user_id: str,
    request: Request,
    tenant_id: str = Depends(require_hotel_tenant),
    ctx: TenantContext = Depends(get_current_user),
    conn=Depends(_platform_connection),
):
    """Revoke access by deactivating the staff account for this hotel."""
    if not _user_belongs_to_tenant(conn, user_id, tenant_id):
        raise HTTPException(status_code=404, detail="Staff member not found")
    if user_id == ctx.user_id:
        raise HTTPException(status_code=400, detail="You cannot revoke your own access")

    actor_code = _actor_role_code(conn, ctx, tenant_id)
    current_role = _staff_role_for_user(conn, user_id, tenant_id)
    target_code = current_role.get("code") if current_role else None
    _assert_can_mutate_target(actor_code, target_code)

    if current_role and current_role.get("code") == HOTEL_OWNER:
        _assert_not_last_owner(conn, tenant_id, user_id)

    conn.execute(
        "UPDATE webclaw_user SET status = ? WHERE id = ?",
        (_STATUS_DISABLED, user_id),
    )
    try:
        conn.execute("DELETE FROM webclaw_session WHERE user_id = ?", (user_id,))
    except Exception:
        pass
    conn.commit()
    row = conn.execute("SELECT * FROM webclaw_user WHERE id = ?", (user_id,)).fetchone()
    return {
        "status": "ok",
        "message": "Access revoked",
        "user": _serialize_user(conn, row, tenant_id),
    }
