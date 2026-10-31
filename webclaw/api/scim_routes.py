"""SCIM 2.0 Users endpoint for enterprise identity provisioning."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db
from auth.passwords import hash_password
from db_access import table_columns

router = APIRouter(prefix="/api/v1/scim/v2", tags=["scim"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _auth_tenant(request: Request, tenant_id: str) -> tuple[dict | None, JSONResponse | None]:
    from admin_growth import ensure_growth_tables, _row

    token = _bearer(request)
    if not token:
        return None, JSONResponse(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "detail": "Bearer token required",
                "status": "401",
            },
            401,
        )
    conn = db.get_connection()
    ensure_growth_tables(conn)
    row = conn.execute(
        """SELECT tenant_id, scim_enabled, scim_bearer_token
           FROM webclaw_tenant_identity WHERE tenant_id = ?""",
        (tenant_id,),
    ).fetchone()
    if not row or not bool(_row(row, "scim_enabled", 1)):
        return None, JSONResponse(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "detail": "SCIM not enabled for tenant",
                "status": "403",
            },
            403,
        )
    stored = str(_row(row, "scim_bearer_token", 2) or "")
    if not stored or stored != token:
        return None, JSONResponse(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "detail": "Invalid SCIM token",
                "status": "401",
            },
            401,
        )
    return {"tenant_id": tenant_id}, None


def _scim_user(row) -> dict:
    from admin_growth import _row

    uid = str(_row(row, "id", 0))
    email = str(_row(row, "email", 1) or "")
    name = str(_row(row, "full_name", 2) or "")
    status = str(_row(row, "status", 3) or "active")
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": uid,
        "userName": email,
        "name": {"formatted": name, "givenName": name.split(" ")[0] if name else ""},
        "displayName": name,
        "emails": [{"value": email, "primary": True, "type": "work"}],
        "active": status == "active",
        "meta": {
            "resourceType": "User",
            "created": _row(row, "created_at", 4),
        },
    }


@router.get("/{tenant_id}/Users")
async def scim_list_users(tenant_id: str, request: Request, filter: str | None = None):
    _, err = _auth_tenant(request, tenant_id)
    if err:
        return err
    conn = db.get_connection()
    cols = set(table_columns(conn, "webclaw_user") or [])
    users = []
    if "tenant_id" in cols:
        rows = conn.execute(
            """SELECT id, email, full_name, status, created_at
               FROM webclaw_user WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 200""",
            (tenant_id,),
        ).fetchall()
    else:
        rows = []
    # Optional filter: userName eq "x"
    email_filter = None
    if filter and "userName" in filter and "eq" in filter:
        parts = filter.split('"')
        if len(parts) >= 2:
            email_filter = parts[1].lower()
    for r in rows:
        from admin_growth import _row

        email = str(_row(r, "email", 1) or "").lower()
        if email_filter and email != email_filter:
            continue
        users.append(_scim_user(r))
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": len(users),
        "startIndex": 1,
        "itemsPerPage": len(users),
        "Resources": users,
    }


@router.get("/{tenant_id}/Users/{user_id}")
async def scim_get_user(tenant_id: str, user_id: str, request: Request):
    _, err = _auth_tenant(request, tenant_id)
    if err:
        return err
    conn = db.get_connection()
    cols = set(table_columns(conn, "webclaw_user") or [])
    if "tenant_id" in cols:
        row = conn.execute(
            """SELECT id, email, full_name, status, created_at
               FROM webclaw_user WHERE id = ? AND tenant_id = ?""",
            (user_id, tenant_id),
        ).fetchone()
    else:
        row = None
    if not row:
        return JSONResponse(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "detail": "User not found",
                "status": "404",
            },
            404,
        )
    return _scim_user(row)


@router.post("/{tenant_id}/Users")
async def scim_create_user(tenant_id: str, request: Request):
    _, err = _auth_tenant(request, tenant_id)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON"}, 400)
    email = ""
    for e in body.get("emails") or []:
        if e.get("primary") or not email:
            email = str(e.get("value") or "").strip().lower()
    email = email or str(body.get("userName") or "").strip().lower()
    name = ""
    if isinstance(body.get("name"), dict):
        name = str(body["name"].get("formatted") or body["name"].get("givenName") or "")
    name = name or str(body.get("displayName") or email.split("@")[0])
    if not email or "@" not in email:
        return JSONResponse({"detail": "userName/email required"}, 400)

    conn = db.get_connection()
    existing = conn.execute(
        "SELECT id FROM webclaw_user WHERE email = ?", (email,)
    ).fetchone()
    if existing:
        return JSONResponse({"detail": "User already exists"}, 409)

    user_id = str(uuid.uuid4())
    temp_pw = uuid.uuid4().hex[:12] + "Aa1"
    cols = set(table_columns(conn, "webclaw_user") or [])
    if "tenant_id" in cols:
        conn.execute(
            """INSERT INTO webclaw_user
               (id, username, email, full_name, password_hash, status, created_at, tenant_id)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                user_id,
                email.split("@")[0],
                email,
                name,
                hash_password(temp_pw),
                _now(),
                tenant_id,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO webclaw_user
               (id, username, email, full_name, password_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?)""",
            (
                user_id,
                email.split("@")[0],
                email,
                name,
                hash_password(temp_pw),
                _now(),
            ),
        )
    conn.commit()
    row = conn.execute(
        "SELECT id, email, full_name, status, created_at FROM webclaw_user WHERE id = ?",
        (user_id,),
    ).fetchone()
    return JSONResponse(_scim_user(row), status_code=201)


@router.patch("/{tenant_id}/Users/{user_id}")
async def scim_patch_user(tenant_id: str, user_id: str, request: Request):
    _, err = _auth_tenant(request, tenant_id)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid JSON"}, 400)
    conn = db.get_connection()
    cols = set(table_columns(conn, "webclaw_user") or [])
    if "tenant_id" in cols:
        row = conn.execute(
            "SELECT id FROM webclaw_user WHERE id = ? AND tenant_id = ?",
            (user_id, tenant_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM webclaw_user WHERE id = ?", (user_id,)
        ).fetchone()
    if not row:
        return JSONResponse({"detail": "User not found"}, 404)

    active = None
    display = None
    for op in body.get("Operations") or []:
        path = str(op.get("path") or "").lower()
        val = op.get("value")
        if path == "active" or (not path and isinstance(val, dict) and "active" in val):
            active = val if isinstance(val, bool) else (val or {}).get("active")
        if path in ("displayname", "name.formatted"):
            display = str(val) if val is not None else None
        if isinstance(val, dict):
            if "active" in val:
                active = val["active"]
            if "displayName" in val:
                display = str(val["displayName"])
    if active is not None:
        conn.execute(
            "UPDATE webclaw_user SET status = ? WHERE id = ?",
            ("active" if active else "disabled", user_id),
        )
    if display is not None:
        conn.execute(
            "UPDATE webclaw_user SET full_name = ? WHERE id = ?",
            (display, user_id),
        )
    conn.commit()
    full = conn.execute(
        "SELECT id, email, full_name, status, created_at FROM webclaw_user WHERE id = ?",
        (user_id,),
    ).fetchone()
    return _scim_user(full)


@router.delete("/{tenant_id}/Users/{user_id}")
async def scim_delete_user(tenant_id: str, user_id: str, request: Request):
    _, err = _auth_tenant(request, tenant_id)
    if err:
        return err
    conn = db.get_connection()
    cols = set(table_columns(conn, "webclaw_user") or [])
    if "tenant_id" in cols:
        conn.execute(
            "UPDATE webclaw_user SET status = 'disabled' WHERE id = ? AND tenant_id = ?",
            (user_id, tenant_id),
        )
    else:
        conn.execute(
            "UPDATE webclaw_user SET status = 'disabled' WHERE id = ?", (user_id,)
        )
    conn.execute("DELETE FROM webclaw_session WHERE user_id = ?", (user_id,))
    conn.commit()
    from fastapi.responses import Response

    return Response(status_code=204)
