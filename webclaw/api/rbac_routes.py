"""RBAC Management API — roles, named permissions, and users.

All endpoints require ``platform.admin`` (SUPER_ADMIN / System Manager).
"""
import json
import secrets
import string
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db
from auth.deps import get_optional_tenant
from db_access import table_columns
from auth.passwords import hash_password, validate_password_strength
from rbac import PERMISSION_CATALOG, ROLE_PERMISSIONS, require_permission

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _actor(request: Request) -> tuple[str | None, str | None]:
    ctx = get_optional_tenant(request)
    if not ctx:
        return None, None
    return ctx.user_id, ctx.email


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _user_tenant_context(conn, user_id: str) -> dict:
    """Home tenant + memberships for directory display."""
    from tenant import load_memberships, load_user_tenant_id

    home = load_user_tenant_id(conn, user_id)
    memberships = []
    try:
        memberships = load_memberships(conn, user_id)
    except Exception:
        memberships = []
    hotels = []
    for m in memberships:
        hotels.append(
            {
                "tenant_id": m.get("tenant_id"),
                "hotel_name": m.get("hotel_name") or m.get("tenant_id"),
                "role": m.get("role") or m.get("role_code"),
                "role_name": m.get("role_name"),
            }
        )
    if home and not any(h.get("tenant_id") == home for h in hotels):
        hotels.insert(0, {"tenant_id": home, "hotel_name": home, "role": None, "role_name": None})
    return {"tenant_id": home, "tenants": hotels}


def _session_count(conn, user_id: str) -> int:
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM webclaw_session WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            or 0
        )
    except Exception:
        return 0


def _audit_user_action(
    request: Request,
    action: str,
    target_user_id: str,
    *,
    before: dict | None = None,
    after: dict | None = None,
    detail: str = "",
) -> None:
    try:
        from admin_ops import write_platform_audit

        uid, email = _actor(request)
        write_platform_audit(
            db.get_connection(),
            user_id=uid,
            user_email=email,
            tenant_id=(after or before or {}).get("tenant_id"),
            action_category="users",
            action=action,
            ip_address=_client_ip(request),
            detail=detail or target_user_id,
            before=before,
            after=after,
        )
    except Exception:
        pass


def _generate_temp_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    # Ensure strength rules: upper, lower, digit
    chars = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
    ]
    chars += [secrets.choice(alphabet) for _ in range(max(0, length - 3))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


# ── Roles ────────────────────────────────────────────────────────────────────

@router.get("/roles")
@require_permission("platform.admin")
async def list_roles(request: Request):
    """List all roles with permission counts."""
    conn = db.get_connection()
    cols = table_columns(conn, "webclaw_role")
    code_sql = "r.code" if "code" in cols else "NULL"
    rows = conn.execute(
        f"""SELECT r.id, r.name, r.description, r.is_system, r.created_at,
                  (SELECT COUNT(*) FROM webclaw_role_permission WHERE role_id = r.id) as perm_count,
                  (SELECT COUNT(*) FROM webclaw_user_role WHERE role_id = r.id) as user_count,
                  {code_sql} as code
           FROM webclaw_role r ORDER BY r.is_system DESC, r.name"""
    ).fetchall()

    roles = []
    for r in rows:
        roles.append({
            "id": r[0], "name": r[1], "description": r[2],
            "is_system": bool(r[3]), "created_at": r[4],
            "permission_count": r[5], "user_count": r[6],
            "code": r[7],
        })
    return {"status": "ok", "roles": roles}


@router.get("/permissions")
@require_permission("platform.admin")
async def list_permission_catalog(request: Request):
    """Named permission catalog and default role grants."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT name, description, scope FROM webclaw_permission ORDER BY name"
        ).fetchall()
        catalog = [
            {"name": r[0], "description": r[1], "scope": r[2]} for r in rows
        ]
    except Exception:
        catalog = [
            {"name": n, "description": spec["description"], "scope": spec["scope"]}
            for n, spec in PERMISSION_CATALOG.items()
        ]
    grants = {code: list(perms) for code, perms in ROLE_PERMISSIONS.items()}
    return {"status": "ok", "permissions": catalog, "role_grants": grants}


@router.post("/roles")
@require_permission("platform.admin")
async def create_role(request: Request):
    """Create a new role."""
    body = await request.json()
    name = body.get("name", "").strip()
    description = body.get("description", "").strip()

    if not name:
        return JSONResponse({"status": "error", "message": "Role name is required"}, 400)

    conn = db.get_connection()
    existing = conn.execute("SELECT id FROM webclaw_role WHERE name = ?", (name,)).fetchone()
    if existing:
        return JSONResponse({"status": "error", "message": f"Role '{name}' already exists"}, 409)

    role_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO webclaw_role (id, name, description, is_system) VALUES (?, ?, ?, 0)",
        (role_id, name, description),
    )
    conn.commit()
    return {"status": "ok", "role": {"id": role_id, "name": name, "description": description}}


@router.put("/roles/{role_id}")
@require_permission("platform.admin")
async def update_role(role_id: str, request: Request):
    """Update a role's name/description."""
    conn = db.get_connection()
    role = conn.execute("SELECT is_system FROM webclaw_role WHERE id = ?", (role_id,)).fetchone()
    if not role:
        return JSONResponse({"status": "error", "message": "Role not found"}, 404)

    body = await request.json()
    name = body.get("name", "").strip()
    description = body.get("description", "").strip()

    if name:
        conn.execute("UPDATE webclaw_role SET name = ?, description = ? WHERE id = ?",
                      (name, description, role_id))
    else:
        conn.execute("UPDATE webclaw_role SET description = ? WHERE id = ?",
                      (description, role_id))
    conn.commit()
    return {"status": "ok", "message": "Role updated"}


@router.delete("/roles/{role_id}")
@require_permission("platform.admin")
async def delete_role(role_id: str, request: Request):
    """Delete a role (system roles cannot be deleted)."""
    conn = db.get_connection()
    role = conn.execute("SELECT name, is_system FROM webclaw_role WHERE id = ?", (role_id,)).fetchone()
    if not role:
        return JSONResponse({"status": "error", "message": "Role not found"}, 404)
    if role[1]:
        return JSONResponse({"status": "error", "message": f"Cannot delete system role '{role[0]}'"}, 403)

    conn.execute("DELETE FROM webclaw_role WHERE id = ?", (role_id,))
    conn.commit()
    return {"status": "ok", "message": "Role deleted"}


# ── Permissions ──────────────────────────────────────────────────────────────

@router.get("/roles/{role_id}/permissions")
@require_permission("platform.admin")
async def list_permissions(role_id: str, request: Request):
    """List permissions for a role."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT id, skill, action_pattern, allowed FROM webclaw_role_permission WHERE role_id = ?",
        (role_id,),
    ).fetchall()

    perms = [{"id": r[0], "skill": r[1], "action_pattern": r[2], "allowed": bool(r[3])} for r in rows]
    return {"status": "ok", "permissions": perms}


@router.post("/roles/{role_id}/permissions")
@require_permission("platform.admin")
async def add_permission(role_id: str, request: Request):
    """Add a permission to a role."""
    body = await request.json()
    skill = body.get("skill", "").strip()
    action_pattern = body.get("action_pattern", "").strip()
    allowed = body.get("allowed", True)

    if not skill or not action_pattern:
        return JSONResponse({"status": "error", "message": "skill and action_pattern required"}, 400)

    conn = db.get_connection()
    perm_id = str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO webclaw_role_permission (id, role_id, skill, action_pattern, allowed) VALUES (?, ?, ?, ?, ?)",
            (perm_id, role_id, skill, action_pattern, 1 if allowed else 0),
        )
        conn.commit()
    except Exception:
        return JSONResponse({"status": "error", "message": "Permission already exists"}, 409)

    return {"status": "ok", "permission": {"id": perm_id, "skill": skill, "action_pattern": action_pattern, "allowed": allowed}}


@router.delete("/roles/{role_id}/permissions/{perm_id}")
@require_permission("platform.admin")
async def remove_permission(role_id: str, perm_id: str, request: Request):
    """Remove a permission from a role."""
    conn = db.get_connection()
    conn.execute("DELETE FROM webclaw_role_permission WHERE id = ? AND role_id = ?", (perm_id, role_id))
    conn.commit()
    return {"status": "ok", "message": "Permission removed"}


# ── Users ────────────────────────────────────────────────────────────────────

@router.get("/users")
@require_permission("platform.admin")
async def list_users(
    request: Request,
    q: str | None = None,
    tenant_id: str | None = None,
    status: str | None = None,
    limit: int = 200,
    offset: int = 0,
):
    """Cross-tenant user directory with optional search and hotel filter."""
    conn = db.get_connection()
    limit = max(1, min(int(limit or 200), 500))
    offset = max(0, int(offset or 0))
    cols = set(table_columns(conn, "webclaw_user") or [])
    has_tenant_col = "tenant_id" in cols

    clauses = ["1=1"]
    params: list = []
    if status in ("active", "disabled"):
        clauses.append("u.status = ?")
        params.append(status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        clauses.append(
            "(u.email LIKE ? OR u.full_name LIKE ? OR u.username LIKE ? OR u.id LIKE ?)"
        )
        params.extend([like, like, like, like])
    if tenant_id and tenant_id.strip():
        tid = tenant_id.strip()
        if has_tenant_col:
            clauses.append(
                """(u.tenant_id = ?
                    OR EXISTS (
                      SELECT 1 FROM webclaw_user_tenant ut
                      WHERE ut.user_id = u.id AND ut.tenant_id = ?
                    ))"""
            )
            params.extend([tid, tid])
        else:
            clauses.append(
                """EXISTS (
                      SELECT 1 FROM webclaw_user_tenant ut
                      WHERE ut.user_id = u.id AND ut.tenant_id = ?
                    )"""
            )
            params.append(tid)

    where = " AND ".join(clauses)
    total = conn.execute(
        f"SELECT COUNT(*) FROM webclaw_user u WHERE {where}", params
    ).fetchone()[0]

    users = conn.execute(
        f"""SELECT u.id, u.username, u.email, u.full_name, u.status,
                   u.last_login, u.created_at
            FROM webclaw_user u
            WHERE {where}
            ORDER BY u.created_at DESC
            LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()

    result = []
    for u in users:
        user_id = u[0] if isinstance(u, tuple) else u["id"]
        roles = conn.execute(
            "SELECT r.id, r.name FROM webclaw_user_role ur JOIN webclaw_role r ON r.id = ur.role_id WHERE ur.user_id = ?",
            (user_id,),
        ).fetchall()
        tctx = _user_tenant_context(conn, user_id)
        result.append({
            "id": user_id,
            "username": u[1] if isinstance(u, tuple) else u["username"],
            "email": u[2] if isinstance(u, tuple) else u["email"],
            "full_name": u[3] if isinstance(u, tuple) else u["full_name"],
            "status": u[4] if isinstance(u, tuple) else u["status"],
            "last_login": u[5] if isinstance(u, tuple) else u["last_login"],
            "created_at": u[6] if isinstance(u, tuple) else u["created_at"],
            "roles": [{"id": r[0], "name": r[1]} for r in roles],
            "tenant_id": tctx.get("tenant_id"),
            "tenants": tctx.get("tenants") or [],
            "active_sessions": _session_count(conn, user_id),
        })

    return {"status": "ok", "users": result, "total": int(total or 0)}


@router.post("/users")
@require_permission("platform.admin")
async def create_user(request: Request):
    """Create a new user with optional role assignments."""
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    full_name = body.get("full_name", "").strip()
    role_ids = body.get("role_ids", [])

    if not email or not password:
        return JSONResponse({"status": "error", "message": "Email and password required"}, 400)

    pw_err = validate_password_strength(password)
    if pw_err:
        return JSONResponse({"status": "error", "message": pw_err}, 400)

    conn = db.get_connection()
    existing = conn.execute("SELECT id FROM webclaw_user WHERE email = ?", (email,)).fetchone()
    if existing:
        return JSONResponse({"status": "error", "message": "Email already registered"}, 409)

    user_id = str(uuid.uuid4())
    username = email.split("@")[0]
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "INSERT INTO webclaw_user (id, username, email, full_name, password_hash, status, created_at) VALUES (?, ?, ?, ?, ?, 'active', ?)",
        (user_id, username, email, full_name or username, hash_password(password), now),
    )

    for rid in role_ids:
        try:
            conn.execute(
                "INSERT INTO webclaw_user_role (id, user_id, role_id) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), user_id, rid),
            )
        except Exception:
            pass

    conn.commit()
    return {"status": "ok", "user": {"id": user_id, "email": email, "full_name": full_name or username}}


@router.put("/users/{user_id}")
@require_permission("platform.admin")
async def update_user(user_id: str, request: Request):
    """Update user details (name, status, password reset)."""
    conn = db.get_connection()
    row = conn.execute(
        "SELECT id, full_name, status, email FROM webclaw_user WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "User not found"}, 404)

    before = {
        "full_name": row[1] if isinstance(row, tuple) else row["full_name"],
        "status": row[2] if isinstance(row, tuple) else row["status"],
        "email": row[3] if isinstance(row, tuple) else row["email"],
    }
    body = await request.json()
    full_name = body.get("full_name")
    status = body.get("status")
    new_password = body.get("password")
    after = dict(before)
    sessions_revoked = 0

    if full_name is not None:
        conn.execute("UPDATE webclaw_user SET full_name = ? WHERE id = ?", (full_name.strip(), user_id))
        after["full_name"] = full_name.strip()
    if status and status in ("active", "disabled"):
        conn.execute("UPDATE webclaw_user SET status = ? WHERE id = ?", (status, user_id))
        after["status"] = status
    if new_password:
        pw_err = validate_password_strength(new_password)
        if pw_err:
            return JSONResponse({"status": "error", "message": pw_err}, 400)
        conn.execute("UPDATE webclaw_user SET password_hash = ? WHERE id = ?",
                      (hash_password(new_password), user_id))
        cur = conn.execute("DELETE FROM webclaw_session WHERE user_id = ?", (user_id,))
        sessions_revoked = cur.rowcount if cur is not None else 0
        after["password_reset"] = True
        after["sessions_revoked"] = sessions_revoked

    conn.commit()
    _audit_user_action(
        request,
        "update_user",
        user_id,
        before=before,
        after=after,
        detail=json.dumps({"user_id": user_id, "email": before.get("email")}),
    )
    return {"status": "ok", "message": "User updated", "sessions_revoked": sessions_revoked}


@router.post("/users/{user_id}/force-logout")
@require_permission("platform.admin")
async def force_logout_user(user_id: str, request: Request):
    """Revoke all active sessions for a user without changing their password."""
    conn = db.get_connection()
    row = conn.execute(
        "SELECT id, email FROM webclaw_user WHERE id = ?", (user_id,)
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "User not found"}, 404)
    email = row[1] if isinstance(row, tuple) else row["email"]
    before_count = _session_count(conn, user_id)
    cur = conn.execute("DELETE FROM webclaw_session WHERE user_id = ?", (user_id,))
    revoked = cur.rowcount if cur is not None else before_count
    conn.commit()
    _audit_user_action(
        request,
        "force_logout",
        user_id,
        before={"active_sessions": before_count, "email": email},
        after={"active_sessions": 0, "email": email},
        detail=f"revoked={revoked}",
    )
    return {"status": "ok", "sessions_revoked": int(revoked or 0)}


@router.post("/users/{user_id}/reset-password")
@require_permission("platform.admin")
async def reset_user_password(user_id: str, request: Request):
    """Set a new password (body.password) or generate a temporary one; revoke sessions."""
    conn = db.get_connection()
    row = conn.execute(
        "SELECT id, email FROM webclaw_user WHERE id = ?", (user_id,)
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "User not found"}, 404)
    email = row[1] if isinstance(row, tuple) else row["email"]

    try:
        body = await request.json()
    except Exception:
        body = {}
    new_password = (body or {}).get("password") or ""
    generated = False
    if not new_password:
        new_password = _generate_temp_password()
        generated = True
    pw_err = validate_password_strength(new_password)
    if pw_err:
        return JSONResponse({"status": "error", "message": pw_err}, 400)

    before_count = _session_count(conn, user_id)
    conn.execute(
        "UPDATE webclaw_user SET password_hash = ?, failed_login_attempts = 0, locked_until = NULL WHERE id = ?",
        (hash_password(new_password), user_id),
    )
    cur = conn.execute("DELETE FROM webclaw_session WHERE user_id = ?", (user_id,))
    revoked = cur.rowcount if cur is not None else before_count
    conn.commit()
    _audit_user_action(
        request,
        "reset_password",
        user_id,
        before={"email": email, "active_sessions": before_count},
        after={"email": email, "active_sessions": 0, "generated": generated},
        detail=f"sessions_revoked={revoked}",
    )
    return {
        "status": "ok",
        "sessions_revoked": int(revoked or 0),
        "temporary_password": new_password if generated else None,
        "generated": generated,
    }


@router.post("/users/{user_id}/roles")
@require_permission("platform.admin")
async def assign_role(user_id: str, request: Request):
    """Assign a role to a user."""
    body = await request.json()
    role_id = body.get("role_id", "").strip()
    if not role_id:
        return JSONResponse({"status": "error", "message": "role_id required"}, 400)

    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO webclaw_user_role (id, user_id, role_id) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), user_id, role_id),
        )
        conn.commit()
    except Exception:
        return JSONResponse({"status": "error", "message": "Role already assigned or not found"}, 409)

    return {"status": "ok", "message": "Role assigned"}


@router.delete("/users/{user_id}/roles/{role_id}")
@require_permission("platform.admin")
async def remove_role(user_id: str, role_id: str, request: Request):
    """Remove a role from a user."""
    conn = db.get_connection()
    conn.execute("DELETE FROM webclaw_user_role WHERE user_id = ? AND role_id = ?", (user_id, role_id))
    conn.commit()
    return {"status": "ok", "message": "Role removed"}
