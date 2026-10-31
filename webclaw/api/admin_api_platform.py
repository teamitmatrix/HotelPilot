"""API / webhook platform admin — keys, rate limits, endpoints, delivery logs.

Operator console for gap #25. Keys are shown in full only once at create time.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

import db
from db_access import DbConn, run_ddl
from rbac import require_any_permission

router = APIRouter(prefix="/api/v1/admin", tags=["admin-api-platform"])

API_PLATFORM_DDL = """
CREATE TABLE IF NOT EXISTS webclaw_api_key (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT,
    name                TEXT NOT NULL,
    key_prefix          TEXT NOT NULL,
    key_hash            TEXT NOT NULL UNIQUE,
    rate_limit_per_min  INTEGER NOT NULL DEFAULT 60,
    scopes              TEXT NOT NULL DEFAULT '["read"]',
    is_active           INTEGER NOT NULL DEFAULT 1,
    last_used_at        TEXT,
    created_at          TEXT NOT NULL,
    created_by          TEXT,
    revoked_at          TEXT,
    revoked_by          TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_key_tenant
    ON webclaw_api_key(tenant_id, is_active);
CREATE INDEX IF NOT EXISTS idx_api_key_prefix
    ON webclaw_api_key(key_prefix);

CREATE TABLE IF NOT EXISTS webclaw_webhook_endpoint (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT,
    name            TEXT NOT NULL,
    url             TEXT NOT NULL,
    secret          TEXT NOT NULL,
    events          TEXT NOT NULL DEFAULT '["*"]',
    is_active       INTEGER NOT NULL DEFAULT 1,
    failure_count   INTEGER NOT NULL DEFAULT 0,
    last_success_at TEXT,
    last_failure_at TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_endpoint_tenant
    ON webclaw_webhook_endpoint(tenant_id, is_active);

CREATE TABLE IF NOT EXISTS webclaw_webhook_delivery (
    id              TEXT PRIMARY KEY,
    endpoint_id     TEXT NOT NULL,
    tenant_id       TEXT,
    event_type      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','success','failed','skipped')),
    attempt         INTEGER NOT NULL DEFAULT 1,
    http_status     INTEGER,
    request_body    TEXT NOT NULL DEFAULT '',
    response_body   TEXT NOT NULL DEFAULT '',
    error           TEXT NOT NULL DEFAULT '',
    duration_ms     INTEGER,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_endpoint
    ON webclaw_webhook_delivery(endpoint_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_status
    ON webclaw_webhook_delivery(status, created_at DESC);

CREATE TABLE IF NOT EXISTS webclaw_api_rate_limit_policy (
    id                  TEXT PRIMARY KEY CHECK (id = 'default'),
    default_per_min     INTEGER NOT NULL DEFAULT 60,
    burst_per_min       INTEGER NOT NULL DEFAULT 120,
    platform_per_min    INTEGER NOT NULL DEFAULT 300,
    updated_at          TEXT,
    updated_by          TEXT
);
"""

DEFAULT_EVENTS = [
    "booking.created",
    "booking.updated",
    "booking.cancelled",
    "folio.closed",
    "guest.created",
    "night_audit.completed",
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


def ensure_api_platform_tables(conn: DbConn) -> None:
    run_ddl(conn, API_PLATFORM_DDL)
    if not conn.execute(
        "SELECT 1 FROM webclaw_api_rate_limit_policy WHERE id = 'default'"
    ).fetchone():
        conn.execute(
            """INSERT INTO webclaw_api_rate_limit_policy
               (id, default_per_min, burst_per_min, platform_per_min, updated_at)
               VALUES ('default', 60, 120, 300, ?)""",
            (_now(),),
        )
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
            action_category="api_platform",
            action=action,
            ip_address=_client_ip(request),
            detail=detail,
        )
    except Exception:
        pass


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, hash). Prefixed hp_live_ for HotelPilot live keys."""
    secret = secrets.token_urlsafe(32)
    full = f"hp_live_{secret}"
    prefix = full[:16]
    return full, prefix, _hash_key(full)


def _mask_secret(secret: str | None) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "••••••••"
    return secret[:4] + "•" * (len(secret) - 8) + secret[-4:]


def _parse_json_list(raw, default=None):
    if default is None:
        default = []
    if raw is None:
        return list(default)
    if isinstance(raw, list):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else list(default)
    except Exception:
        return list(default)


def _hotel_name(tenant_id: str | None) -> str | None:
    if not tenant_id:
        return None
    erp = db.get_skill_db("erpclaw")
    if not erp:
        return None
    try:
        r = erp.execute(
            "SELECT hotel_name FROM pms_tenant WHERE id = ?", (tenant_id,)
        ).fetchone()
        return _row(r, "hotel_name", 0) if r else None
    except Exception:
        return None
    finally:
        erp.close()


def serialize_key(r) -> dict:
    tid = _row(r, "tenant_id", 1)
    return {
        "id": _row(r, "id", 0),
        "tenant_id": tid,
        "hotel_name": _hotel_name(tid) if tid else None,
        "name": _row(r, "name", 2),
        "key_prefix": _row(r, "key_prefix", 3),
        "rate_limit_per_min": int(_row(r, "rate_limit_per_min", 5) or 60),
        "scopes": _parse_json_list(_row(r, "scopes", 6), ["read"]),
        "is_active": bool(_row(r, "is_active", 7)),
        "last_used_at": _row(r, "last_used_at", 8),
        "created_at": _row(r, "created_at", 9),
        "created_by": _row(r, "created_by", 10),
        "revoked_at": _row(r, "revoked_at", 11),
    }


def serialize_endpoint(r, *, include_secret: bool = False) -> dict:
    tid = _row(r, "tenant_id", 1)
    secret = _row(r, "secret", 4)
    return {
        "id": _row(r, "id", 0),
        "tenant_id": tid,
        "hotel_name": _hotel_name(tid) if tid else None,
        "name": _row(r, "name", 2),
        "url": _row(r, "url", 3),
        "secret": secret if include_secret else _mask_secret(secret),
        "secret_configured": bool(secret),
        "events": _parse_json_list(_row(r, "events", 5), ["*"]),
        "is_active": bool(_row(r, "is_active", 6)),
        "failure_count": int(_row(r, "failure_count", 7) or 0),
        "last_success_at": _row(r, "last_success_at", 8),
        "last_failure_at": _row(r, "last_failure_at", 9),
        "created_at": _row(r, "created_at", 10),
        "updated_at": _row(r, "updated_at", 11),
    }


def get_rate_limit_policy(conn: DbConn) -> dict:
    ensure_api_platform_tables(conn)
    r = conn.execute(
        """SELECT default_per_min, burst_per_min, platform_per_min, updated_at, updated_by
           FROM webclaw_api_rate_limit_policy WHERE id = 'default'"""
    ).fetchone()
    return {
        "default_per_min": int(_row(r, "default_per_min", 0) or 60),
        "burst_per_min": int(_row(r, "burst_per_min", 1) or 120),
        "platform_per_min": int(_row(r, "platform_per_min", 2) or 300),
        "updated_at": _row(r, "updated_at", 3),
        "updated_by": _row(r, "updated_by", 4),
    }


def deliver_webhook(
    conn: DbConn,
    endpoint_id: str,
    *,
    event_type: str = "test.ping",
    payload: dict | None = None,
) -> dict:
    """Send one outbound webhook and write a delivery log row."""
    ensure_api_platform_tables(conn)
    ep = conn.execute(
        "SELECT * FROM webclaw_webhook_endpoint WHERE id = ?", (endpoint_id,)
    ).fetchone()
    if not ep:
        return {"ok": False, "error": "Endpoint not found", "http_status": 404}
    if not bool(_row(ep, "is_active", 6)):
        return {"ok": False, "error": "Endpoint is inactive", "http_status": 400}

    url = str(_row(ep, "url", 3))
    secret = str(_row(ep, "secret", 4) or "")
    tenant_id = _row(ep, "tenant_id", 1)
    body = payload or {
        "event": event_type,
        "tenant_id": tenant_id,
        "sent_at": _now(),
        "data": {"message": "HotelPilot webhook test"},
    }
    body_json = json.dumps(body)
    sig = hashlib.sha256(f"{secret}.{body_json}".encode("utf-8")).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "HotelPilot-Webhooks/1.0",
        "X-HotelPilot-Event": event_type,
        "X-HotelPilot-Signature": sig,
    }

    status = "failed"
    http_status = None
    response_body = ""
    error = ""
    duration_ms = None
    started = datetime.now(timezone.utc)
    try:
        import urllib.request

        req = urllib.request.Request(
            url, data=body_json.encode("utf-8"), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            http_status = getattr(resp, "status", None) or resp.getcode()
            response_body = (resp.read() or b"")[:2000].decode("utf-8", errors="replace")
            status = "success" if 200 <= int(http_status) < 300 else "failed"
            if status == "failed":
                error = f"HTTP {http_status}"
    except Exception as e:
        error = str(e)[:500]
        # urllib raises HTTPError with code
        code = getattr(e, "code", None)
        if code is not None:
            http_status = int(code)
            try:
                response_body = (e.read() or b"")[:2000].decode("utf-8", errors="replace")  # type: ignore[attr-defined]
            except Exception:
                pass
    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    now = _now()
    delivery_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO webclaw_webhook_delivery
           (id, endpoint_id, tenant_id, event_type, status, attempt, http_status,
            request_body, response_body, error, duration_ms, created_at)
           VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
        (
            delivery_id,
            endpoint_id,
            tenant_id,
            event_type,
            status,
            http_status,
            body_json[:4000],
            response_body[:2000],
            error,
            duration_ms,
            now,
        ),
    )
    if status == "success":
        conn.execute(
            """UPDATE webclaw_webhook_endpoint
               SET failure_count = 0, last_success_at = ?, updated_at = ?
               WHERE id = ?""",
            (now, now, endpoint_id),
        )
    else:
        conn.execute(
            """UPDATE webclaw_webhook_endpoint
               SET failure_count = failure_count + 1, last_failure_at = ?, updated_at = ?
               WHERE id = ?""",
            (now, now, endpoint_id),
        )
    conn.commit()
    return {
        "ok": status == "success",
        "delivery_id": delivery_id,
        "status": status,
        "http_status": http_status,
        "error": error or None,
        "duration_ms": duration_ms,
    }


# ── Request models ────────────────────────────────────────────────────────────

class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    tenant_id: str | None = Field(default=None, max_length=80)
    rate_limit_per_min: int = Field(default=60, ge=1, le=10000)
    scopes: list[str] = Field(default_factory=lambda: ["read"])


class UpdateApiKeyRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    rate_limit_per_min: int | None = Field(default=None, ge=1, le=10000)
    scopes: list[str] | None = None
    is_active: bool | None = None


class RateLimitPolicyUpdate(BaseModel):
    default_per_min: int | None = Field(default=None, ge=1, le=10000)
    burst_per_min: int | None = Field(default=None, ge=1, le=20000)
    platform_per_min: int | None = Field(default=None, ge=1, le=50000)


class CreateWebhookRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    url: str = Field(min_length=8, max_length=500)
    tenant_id: str | None = Field(default=None, max_length=80)
    events: list[str] = Field(default_factory=lambda: ["*"])
    secret: str | None = Field(default=None, max_length=200)

    @field_validator("url")
    @classmethod
    def url_ok(cls, v: str) -> str:
        value = v.strip()
        if not (value.startswith("https://") or value.startswith("http://")):
            raise ValueError("url must start with http:// or https://")
        return value


class UpdateWebhookRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    url: str | None = Field(default=None, max_length=500)
    events: list[str] | None = None
    is_active: bool | None = None
    secret: str | None = Field(default=None, max_length=200)

    @field_validator("url")
    @classmethod
    def url_ok(cls, v: str | None) -> str | None:
        if v is None:
            return v
        value = v.strip()
        if not (value.startswith("https://") or value.startswith("http://")):
            raise ValueError("url must start with http:// or https://")
        return value


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/api-platform/overview")
@require_any_permission(
    "platform.settings.manage", "platform.tenants.view", "platform.admin"
)
async def api_platform_overview(request: Request):
    conn = db.get_connection()
    ensure_api_platform_tables(conn)
    keys_active = int(
        conn.execute(
            "SELECT COUNT(*) FROM webclaw_api_key WHERE is_active = 1 AND revoked_at IS NULL"
        ).fetchone()[0]
        or 0
    )
    keys_total = int(conn.execute("SELECT COUNT(*) FROM webclaw_api_key").fetchone()[0] or 0)
    hooks_active = int(
        conn.execute(
            "SELECT COUNT(*) FROM webclaw_webhook_endpoint WHERE is_active = 1"
        ).fetchone()[0]
        or 0
    )
    hooks_failing = int(
        conn.execute(
            "SELECT COUNT(*) FROM webclaw_webhook_endpoint WHERE failure_count > 0 AND is_active = 1"
        ).fetchone()[0]
        or 0
    )
    deliveries_failed = int(
        conn.execute(
            """SELECT COUNT(*) FROM webclaw_webhook_delivery
               WHERE status = 'failed'
                 AND created_at >= ?""",
            ((datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).isoformat()),),
        ).fetchone()[0]
        or 0
    )
    return {
        "status": "ok",
        "overview": {
            "keys_active": keys_active,
            "keys_total": keys_total,
            "webhooks_active": hooks_active,
            "webhooks_failing": hooks_failing,
            "deliveries_failed_today": deliveries_failed,
            "event_catalog": DEFAULT_EVENTS,
        },
        "rate_limits": get_rate_limit_policy(conn),
    }


@router.get("/api-platform/rate-limits")
@require_any_permission(
    "platform.settings.manage", "platform.tenants.view", "platform.admin"
)
async def get_rate_limits(request: Request):
    conn = db.get_connection()
    return {"status": "ok", "policy": get_rate_limit_policy(conn)}


@router.put("/api-platform/rate-limits")
@require_any_permission("platform.settings.manage", "platform.admin")
async def update_rate_limits(request: Request):
    try:
        body = await request.json()
        req = RateLimitPolicyUpdate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    current = get_rate_limit_policy(conn)
    patch = req.model_dump(exclude_none=True)
    current.update(patch)
    _, email = _actor(request)
    conn.execute(
        """INSERT INTO webclaw_api_rate_limit_policy
           (id, default_per_min, burst_per_min, platform_per_min, updated_at, updated_by)
           VALUES ('default', ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             default_per_min = excluded.default_per_min,
             burst_per_min = excluded.burst_per_min,
             platform_per_min = excluded.platform_per_min,
             updated_at = excluded.updated_at,
             updated_by = excluded.updated_by""",
        (
            int(current["default_per_min"]),
            int(current["burst_per_min"]),
            int(current["platform_per_min"]),
            _now(),
            email,
        ),
    )
    conn.commit()
    _audit(conn, request, "update_rate_limits", json.dumps(current))
    return {"status": "ok", "policy": get_rate_limit_policy(conn)}


@router.get("/api-platform/keys")
@require_any_permission(
    "platform.settings.manage", "platform.tenants.view", "platform.admin"
)
async def list_api_keys(request: Request, tenant_id: str | None = None):
    conn = db.get_connection()
    ensure_api_platform_tables(conn)
    if tenant_id:
        rows = conn.execute(
            """SELECT * FROM webclaw_api_key WHERE tenant_id = ?
               ORDER BY created_at DESC LIMIT 200""",
            (tenant_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM webclaw_api_key ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    return {"status": "ok", "keys": [serialize_key(r) for r in rows]}


@router.post("/api-platform/keys")
@require_any_permission("platform.settings.manage", "platform.admin")
async def create_api_key(request: Request):
    try:
        body = await request.json()
        req = CreateApiKeyRequest(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_api_platform_tables(conn)
    policy = get_rate_limit_policy(conn)
    rate = req.rate_limit_per_min or policy["default_per_min"]
    full, prefix, key_hash = _generate_api_key()
    kid = str(uuid.uuid4())
    _, email = _actor(request)
    now = _now()
    conn.execute(
        """INSERT INTO webclaw_api_key
           (id, tenant_id, name, key_prefix, key_hash, rate_limit_per_min, scopes,
            is_active, last_used_at, created_at, created_by, revoked_at, revoked_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, ?, NULL, NULL)""",
        (
            kid,
            req.tenant_id,
            req.name.strip(),
            prefix,
            key_hash,
            rate,
            json.dumps(req.scopes or ["read"]),
            now,
            email,
        ),
    )
    conn.commit()
    _audit(conn, request, "create_api_key", f"{kid}:{prefix}", req.tenant_id)
    key = serialize_key(
        conn.execute("SELECT * FROM webclaw_api_key WHERE id = ?", (kid,)).fetchone()
    )
    return {
        "status": "ok",
        "key": key,
        "secret": full,  # shown once
        "message": "Copy this API key now — it will not be shown again.",
    }


@router.patch("/api-platform/keys/{key_id}")
@require_any_permission("platform.settings.manage", "platform.admin")
async def update_api_key(key_id: str, request: Request):
    try:
        body = await request.json()
        req = UpdateApiKeyRequest(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_api_platform_tables(conn)
    row = conn.execute(
        "SELECT id FROM webclaw_api_key WHERE id = ?", (key_id,)
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "API key not found"}, 404)
    patch = req.model_dump(exclude_none=True)
    if "scopes" in patch:
        patch["scopes"] = json.dumps(patch["scopes"])
    if "is_active" in patch:
        patch["is_active"] = 1 if patch["is_active"] else 0
    if not patch:
        return {"status": "ok"}
    sets = [f"{k} = ?" for k in patch.keys()]
    params = list(patch.values()) + [key_id]
    conn.execute(f"UPDATE webclaw_api_key SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    _audit(conn, request, "update_api_key", key_id)
    row = conn.execute("SELECT * FROM webclaw_api_key WHERE id = ?", (key_id,)).fetchone()
    return {"status": "ok", "key": serialize_key(row)}


@router.post("/api-platform/keys/{key_id}/revoke")
@require_any_permission("platform.settings.manage", "platform.admin")
async def revoke_api_key(key_id: str, request: Request):
    conn = db.get_connection()
    ensure_api_platform_tables(conn)
    row = conn.execute(
        "SELECT id, revoked_at FROM webclaw_api_key WHERE id = ?", (key_id,)
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "API key not found"}, 404)
    if _row(row, "revoked_at", 1):
        return {"status": "ok", "message": "Already revoked"}
    _, email = _actor(request)
    now = _now()
    conn.execute(
        """UPDATE webclaw_api_key
           SET is_active = 0, revoked_at = ?, revoked_by = ?
           WHERE id = ?""",
        (now, email, key_id),
    )
    conn.commit()
    _audit(conn, request, "revoke_api_key", key_id)
    return {"status": "ok", "message": "API key revoked"}


@router.get("/api-platform/webhooks")
@require_any_permission(
    "platform.settings.manage", "platform.tenants.view", "platform.admin"
)
async def list_webhooks(request: Request, tenant_id: str | None = None):
    conn = db.get_connection()
    ensure_api_platform_tables(conn)
    if tenant_id:
        rows = conn.execute(
            """SELECT * FROM webclaw_webhook_endpoint WHERE tenant_id = ?
               ORDER BY updated_at DESC LIMIT 200""",
            (tenant_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM webclaw_webhook_endpoint ORDER BY updated_at DESC LIMIT 200"
        ).fetchall()
    return {"status": "ok", "webhooks": [serialize_endpoint(r) for r in rows]}


@router.post("/api-platform/webhooks")
@require_any_permission("platform.settings.manage", "platform.admin")
async def create_webhook(request: Request):
    try:
        body = await request.json()
        req = CreateWebhookRequest(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_api_platform_tables(conn)
    wid = str(uuid.uuid4())
    now = _now()
    secret = (req.secret or "").strip() or secrets.token_urlsafe(24)
    conn.execute(
        """INSERT INTO webclaw_webhook_endpoint
           (id, tenant_id, name, url, secret, events, is_active, failure_count,
            last_success_at, last_failure_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, 0, NULL, NULL, ?, ?)""",
        (
            wid,
            req.tenant_id,
            req.name.strip(),
            req.url.strip(),
            secret,
            json.dumps(req.events or ["*"]),
            now,
            now,
        ),
    )
    conn.commit()
    _audit(conn, request, "create_webhook", wid, req.tenant_id)
    row = conn.execute(
        "SELECT * FROM webclaw_webhook_endpoint WHERE id = ?", (wid,)
    ).fetchone()
    return {
        "status": "ok",
        "webhook": serialize_endpoint(row),
        "secret": secret,  # shown once if auto-generated
    }


@router.patch("/api-platform/webhooks/{webhook_id}")
@require_any_permission("platform.settings.manage", "platform.admin")
async def update_webhook(webhook_id: str, request: Request):
    try:
        body = await request.json()
        req = UpdateWebhookRequest(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_api_platform_tables(conn)
    if not conn.execute(
        "SELECT 1 FROM webclaw_webhook_endpoint WHERE id = ?", (webhook_id,)
    ).fetchone():
        return JSONResponse({"status": "error", "message": "Webhook not found"}, 404)
    patch = req.model_dump(exclude_none=True)
    if "events" in patch:
        patch["events"] = json.dumps(patch["events"])
    if "is_active" in patch:
        patch["is_active"] = 1 if patch["is_active"] else 0
    if "secret" in patch and (not patch["secret"] or str(patch["secret"]).startswith("•")):
        patch.pop("secret")
    if not patch:
        return {"status": "ok"}
    patch["updated_at"] = _now()
    sets = [f"{k} = ?" for k in patch.keys()]
    params = list(patch.values()) + [webhook_id]
    conn.execute(
        f"UPDATE webclaw_webhook_endpoint SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    conn.commit()
    _audit(conn, request, "update_webhook", webhook_id)
    row = conn.execute(
        "SELECT * FROM webclaw_webhook_endpoint WHERE id = ?", (webhook_id,)
    ).fetchone()
    return {"status": "ok", "webhook": serialize_endpoint(row)}


@router.delete("/api-platform/webhooks/{webhook_id}")
@require_any_permission("platform.settings.manage", "platform.admin")
async def delete_webhook(webhook_id: str, request: Request):
    conn = db.get_connection()
    ensure_api_platform_tables(conn)
    conn.execute("DELETE FROM webclaw_webhook_endpoint WHERE id = ?", (webhook_id,))
    conn.commit()
    _audit(conn, request, "delete_webhook", webhook_id)
    return {"status": "ok"}


@router.post("/api-platform/webhooks/{webhook_id}/test")
@require_any_permission("platform.settings.manage", "platform.admin")
async def test_webhook(webhook_id: str, request: Request):
    conn = db.get_connection()
    result = deliver_webhook(conn, webhook_id, event_type="test.ping")
    if result.get("http_status") == 404 and not result.get("ok"):
        return JSONResponse({"status": "error", "message": result.get("error")}, 404)
    _audit(conn, request, "test_webhook", f"{webhook_id}:{result.get('status')}")
    return {"status": "ok", "delivery": result}


@router.get("/api-platform/deliveries")
@require_any_permission(
    "platform.settings.manage", "platform.tenants.view", "platform.admin"
)
async def list_deliveries(
    request: Request,
    endpoint_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
):
    conn = db.get_connection()
    ensure_api_platform_tables(conn)
    limit = max(1, min(int(limit or 100), 500))
    clauses = []
    params: list = []
    if endpoint_id:
        clauses.append("endpoint_id = ?")
        params.append(endpoint_id)
    if status and status != "all":
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"""SELECT id, endpoint_id, tenant_id, event_type, status, attempt,
                   http_status, error, duration_ms, created_at, request_body
            FROM webclaw_webhook_delivery {where}
            ORDER BY created_at DESC LIMIT ?""",
        params,
    ).fetchall()
    return {
        "status": "ok",
        "deliveries": [
            {
                "id": _row(r, "id", 0),
                "endpoint_id": _row(r, "endpoint_id", 1),
                "tenant_id": _row(r, "tenant_id", 2),
                "event_type": _row(r, "event_type", 3),
                "status": _row(r, "status", 4),
                "attempt": _row(r, "attempt", 5),
                "http_status": _row(r, "http_status", 6),
                "error": _row(r, "error", 7),
                "duration_ms": _row(r, "duration_ms", 8),
                "created_at": _row(r, "created_at", 9),
                "request_body": (_row(r, "request_body", 10) or "")[:500],
            }
            for r in rows
        ],
    }


def lookup_api_key(conn: DbConn, raw_key: str) -> dict | None:
    """Validate a raw API key for future external API auth. Returns key row dict or None."""
    ensure_api_platform_tables(conn)
    if not raw_key or not raw_key.startswith("hp_live_"):
        return None
    h = _hash_key(raw_key)
    r = conn.execute(
        """SELECT * FROM webclaw_api_key
           WHERE key_hash = ? AND is_active = 1 AND revoked_at IS NULL""",
        (h,),
    ).fetchone()
    if not r:
        return None
    key = serialize_key(r)
    tid = key.get("tenant_id")
    if tid:
        try:
            from admin_growth import assert_within_plan_limits, record_usage

            limit_err = assert_within_plan_limits(str(tid), "api_calls", delta=1)
            if limit_err:
                return None
            record_usage(str(tid), "api_calls", 1)
        except Exception:
            pass
    conn.execute(
        "UPDATE webclaw_api_key SET last_used_at = ? WHERE id = ?",
        (_now(), _row(r, "id", 0)),
    )
    conn.commit()
    return key
