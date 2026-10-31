"""P2 platform growth APIs — flags, usage, white-label, SSO/SCIM, compliance, partners.

All mutating routes require platform permissions.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

import db
from auth.deps import get_optional_tenant
from db_access import DbConn, run_ddl, table_columns
from rbac import require_any_permission

router = APIRouter(prefix="/api/v1/admin", tags=["admin-growth"])

GROWTH_DDL = """
CREATE TABLE IF NOT EXISTS webclaw_feature_flag (
    key             TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    enabled_global  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webclaw_feature_flag_tenant (
    flag_key        TEXT NOT NULL,
    tenant_id       TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (flag_key, tenant_id)
);

CREATE TABLE IF NOT EXISTS webclaw_usage_addon (
    id              TEXT PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    metric          TEXT NOT NULL
                    CHECK(metric IN ('rooms','staff','api_calls','storage_gb','ai_tokens')),
    unit_price      TEXT NOT NULL DEFAULT '0',
    included_units  INTEGER NOT NULL DEFAULT 0,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webclaw_tenant_addon (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    addon_id        TEXT NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    UNIQUE(tenant_id, addon_id)
);

CREATE TABLE IF NOT EXISTS webclaw_tenant_branding (
    tenant_id       TEXT PRIMARY KEY,
    brand_name      TEXT,
    logo_url        TEXT,
    primary_color   TEXT,
    custom_domain   TEXT,
    email_from_name TEXT,
    email_from_address TEXT,
    support_url     TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webclaw_tenant_identity (
    tenant_id       TEXT PRIMARY KEY,
    sso_enabled     INTEGER NOT NULL DEFAULT 0,
    sso_protocol    TEXT NOT NULL DEFAULT 'oidc'
                    CHECK(sso_protocol IN ('oidc','saml')),
    issuer_url      TEXT,
    client_id       TEXT,
    client_secret   TEXT,
    metadata_url    TEXT,
    scim_enabled    INTEGER NOT NULL DEFAULT 0,
    scim_bearer_token TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webclaw_compliance_policy (
    id                      TEXT PRIMARY KEY CHECK (id = 'default'),
    retention_days          INTEGER NOT NULL DEFAULT 365,
    auto_purge_cancelled    INTEGER NOT NULL DEFAULT 0,
    dpa_contact_email       TEXT,
    updated_at              TEXT,
    updated_by              TEXT
);

CREATE TABLE IF NOT EXISTS webclaw_dsar_request (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    hotel_name      TEXT,
    request_type    TEXT NOT NULL
                    CHECK(request_type IN ('export','erasure')),
    subject_email   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open','in_progress','completed','rejected')),
    notes           TEXT NOT NULL DEFAULT '',
    created_by      TEXT,
    created_by_email TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_dsar_status
    ON webclaw_dsar_request(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS webclaw_usage_counter (
    tenant_id       TEXT NOT NULL,
    metric          TEXT NOT NULL
                    CHECK(metric IN ('api_calls','ai_tokens','storage_gb')),
    period_ym       TEXT NOT NULL,
    value           REAL NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, metric, period_ym)
);

CREATE TABLE IF NOT EXISTS webclaw_usage_alert (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    metric          TEXT NOT NULL,
    period_ym       TEXT NOT NULL,
    threshold_pct   INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(tenant_id, metric, period_ym, threshold_pct)
);

CREATE TABLE IF NOT EXISTS webclaw_partner (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    contact_email   TEXT NOT NULL,
    commission_percent REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active','inactive')),
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webclaw_partner_tenant (
    partner_id      TEXT NOT NULL,
    tenant_id       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (partner_id, tenant_id)
);
"""

DEFAULT_FLAGS = (
    {
        "key": "channel_manager_kill",
        "label": "Kill switch: OTA sync",
        "description": "Force-disable channel manager for all or selected hotels",
        "enabled_global": False,
    },
    {
        "key": "ai_chat_kill",
        "label": "Kill switch: AI chat",
        "description": "Disable in-app AI assistant",
        "enabled_global": False,
    },
    {
        "key": "new_checkout_flow",
        "label": "New guest checkout flow",
        "description": "Progressive rollout of redesigned checkout",
        "enabled_global": False,
    },
    {
        "key": "night_audit_v2",
        "label": "Night audit v2",
        "description": "Enable redesigned night-audit pipeline",
        "enabled_global": False,
    },
)

DEFAULT_ADDONS = (
    {
        "code": "extra_rooms_10",
        "name": "Extra rooms (+10)",
        "metric": "rooms",
        "unit_price": "29",
        "included_units": 10,
    },
    {
        "code": "extra_staff_5",
        "name": "Extra staff (+5)",
        "metric": "staff",
        "unit_price": "19",
        "included_units": 5,
    },
    {
        "code": "api_pack",
        "name": "API call pack",
        "metric": "api_calls",
        "unit_price": "49",
        "included_units": 10000,
    },
    {
        "code": "storage_pack",
        "name": "Storage pack (+5 GB)",
        "metric": "storage_gb",
        "unit_price": "9",
        "included_units": 5,
    },
    {
        "code": "ai_pack",
        "name": "AI token pack (+500k)",
        "metric": "ai_tokens",
        "unit_price": "39",
        "included_units": 500000,
    },
)

# Soft plan defaults when no plan feature stores meters (units / month)
DEFAULT_METER_CAPS = {
    "starter": {"api_calls": 10000, "ai_tokens": 100000, "storage_gb": 5},
    "business": {"api_calls": 100000, "ai_tokens": 1000000, "storage_gb": 50},
    "professional": {"api_calls": 100000, "ai_tokens": 1000000, "storage_gb": 50},
    "enterprise": {"api_calls": 0, "ai_tokens": 0, "storage_gb": 0},  # 0 = unlimited
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actor(request: Request) -> tuple[str | None, str | None]:
    ctx = get_optional_tenant(request)
    if not ctx:
        return None, None
    return ctx.user_id, ctx.email


def _row(row, *keys_or_idx):
    if row is None:
        return None
    if hasattr(row, "keys"):
        key = keys_or_idx[0] if isinstance(keys_or_idx[0], str) else None
        if key and key in row.keys():
            return row[key]
        for k in keys_or_idx:
            if isinstance(k, str) and k in row.keys():
                return row[k]
        return None
    idx = keys_or_idx[-1] if isinstance(keys_or_idx[-1], int) else 0
    try:
        return row[idx]
    except Exception:
        return None


def _widen_usage_addon_metric_check(conn: DbConn) -> None:
    """Postgres tables created before ai_tokens need the CHECK widened."""
    try:
        conn.execute(
            "ALTER TABLE webclaw_usage_addon "
            "DROP CONSTRAINT IF EXISTS webclaw_usage_addon_metric_check"
        )
        conn.execute(
            """ALTER TABLE webclaw_usage_addon
               ADD CONSTRAINT webclaw_usage_addon_metric_check
               CHECK (metric IN ('rooms','staff','api_calls','storage_gb','ai_tokens'))"""
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    try:
        conn.execute(
            "ALTER TABLE webclaw_usage_counter "
            "DROP CONSTRAINT IF EXISTS webclaw_usage_counter_metric_check"
        )
        conn.execute(
            """ALTER TABLE webclaw_usage_counter
               ADD CONSTRAINT webclaw_usage_counter_metric_check
               CHECK (metric IN ('api_calls','ai_tokens','storage_gb'))"""
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def ensure_growth_tables(conn: DbConn) -> None:
    run_ddl(conn, GROWTH_DDL)
    _widen_usage_addon_metric_check(conn)
    now = _now()
    for spec in DEFAULT_FLAGS:
        exists = conn.execute(
            "SELECT 1 FROM webclaw_feature_flag WHERE key = ?", (spec["key"],)
        ).fetchone()
        if not exists:
            conn.execute(
                """INSERT INTO webclaw_feature_flag
                   (key, label, description, enabled_global, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    spec["key"],
                    spec["label"],
                    spec["description"],
                    1 if spec["enabled_global"] else 0,
                    now,
                    now,
                ),
            )
    for spec in DEFAULT_ADDONS:
        exists = conn.execute(
            "SELECT 1 FROM webclaw_usage_addon WHERE code = ?", (spec["code"],)
        ).fetchone()
        if not exists:
            try:
                conn.execute(
                    """INSERT INTO webclaw_usage_addon
                       (id, code, name, metric, unit_price, included_units, is_active, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        spec["code"],
                        spec["name"],
                        spec["metric"],
                        spec["unit_price"],
                        spec["included_units"],
                        now,
                        now,
                    ),
                )
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                _widen_usage_addon_metric_check(conn)
                try:
                    conn.execute(
                        """INSERT INTO webclaw_usage_addon
                           (id, code, name, metric, unit_price, included_units, is_active, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                        (
                            str(uuid.uuid4()),
                            spec["code"],
                            spec["name"],
                            spec["metric"],
                            spec["unit_price"],
                            spec["included_units"],
                            now,
                            now,
                        ),
                    )
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
    if not conn.execute(
        "SELECT 1 FROM webclaw_compliance_policy WHERE id = 'default'"
    ).fetchone():
        conn.execute(
            """INSERT INTO webclaw_compliance_policy
               (id, retention_days, auto_purge_cancelled, dpa_contact_email, updated_at)
               VALUES ('default', 365, 0, '', ?)""",
            (now,),
        )
    brand_cols = set(table_columns(conn, "webclaw_tenant_branding") or [])
    for col, ddl in (
        ("dns_status", "ALTER TABLE webclaw_tenant_branding ADD COLUMN dns_status TEXT"),
        ("dns_verified", "ALTER TABLE webclaw_tenant_branding ADD COLUMN dns_verified INTEGER NOT NULL DEFAULT 0"),
        ("dns_checked_at", "ALTER TABLE webclaw_tenant_branding ADD COLUMN dns_checked_at TEXT"),
        ("dns_target", "ALTER TABLE webclaw_tenant_branding ADD COLUMN dns_target TEXT"),
    ):
        if col not in brand_cols:
            try:
                conn.execute(ddl)
                brand_cols.add(col)
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
    partner_cols = set(table_columns(conn, "webclaw_partner") or [])
    if "user_id" not in partner_cols:
        try:
            conn.execute("ALTER TABLE webclaw_partner ADD COLUMN user_id TEXT")
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    conn.commit()


def _audit(conn, request: Request, category: str, action: str, detail: str = "", tenant_id=None):
    try:
        from admin_ops import write_platform_audit, _client_ip

        uid, email = _actor(request)
        write_platform_audit(
            conn,
            user_id=uid,
            user_email=email,
            tenant_id=tenant_id,
            action_category=category,
            action=action,
            ip_address=_client_ip(request),
            detail=detail,
        )
    except Exception:
        pass


def _erp():
    return db.get_skill_db("erpclaw")


def _tenant_usage(conn: DbConn, tenant_id: str) -> dict:
    """Rooms/staff usage vs plan limits + addon headroom."""
    from admin_platform import (
        _erp as erp_fn,
        _get_billing_override,
        _get_tenant_row,
        _load_plan_map,
        _serialize_tenant,
        _table_exists,
    )

    erp = erp_fn()
    rooms = 0
    staff = 0
    hotel = None
    if erp:
        try:
            row = _get_tenant_row(erp, tenant_id)
            if row:
                plans = _load_plan_map(conn)
                billing = _get_billing_override(conn, tenant_id)
                hotel = _serialize_tenant(row, plans, billing)
                tid = str(hotel["id"])
                if _table_exists(erp, "hospitalityclaw_room"):
                    rooms = int(
                        erp.execute(
                            "SELECT COUNT(*) FROM hospitalityclaw_room WHERE tenant_id = ?",
                            (tid,),
                        ).fetchone()[0]
                        or 0
                    )
                cols = table_columns(conn, "webclaw_user") or []
                if "tenant_id" in cols:
                    staff = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM webclaw_user WHERE tenant_id = ? AND status != 'deleted'",
                            (tid,),
                        ).fetchone()[0]
                        or 0
                    )
        finally:
            erp.close()

    addon_rooms = 0
    addon_staff = 0
    addon_api = 0
    addon_ai = 0
    addon_storage = 0
    ensure_growth_tables(conn)
    for r in conn.execute(
        """SELECT a.metric, a.included_units, ta.quantity
           FROM webclaw_tenant_addon ta
           JOIN webclaw_usage_addon a ON a.id = ta.addon_id
           WHERE ta.tenant_id = ? AND a.is_active = 1""",
        (tenant_id,),
    ).fetchall():
        metric = _row(r, "metric", 0)
        units = int(_row(r, "included_units", 1) or 0)
        qty = int(_row(r, "quantity", 2) or 1)
        if metric == "rooms":
            addon_rooms += units * qty
        elif metric == "staff":
            addon_staff += units * qty
        elif metric == "api_calls":
            addon_api += units * qty
        elif metric == "ai_tokens":
            addon_ai += units * qty
        elif metric == "storage_gb":
            addon_storage += units * qty

    max_rooms = int((hotel or {}).get("max_rooms") or 0)
    max_staff = int((hotel or {}).get("max_staff") or 0)
    rooms_cap = None if max_rooms <= 0 else max_rooms + addon_rooms
    staff_cap = None if max_staff <= 0 else max_staff + addon_staff

    plan_code = str((hotel or {}).get("plan_code") or "starter")
    meter_defaults = DEFAULT_METER_CAPS.get(plan_code) or DEFAULT_METER_CAPS["starter"]
    period = _period_ym()
    api_used = _counter_value(conn, tenant_id, "api_calls", period)
    ai_used = _counter_value(conn, tenant_id, "ai_tokens", period)
    storage_used = _counter_value(conn, tenant_id, "storage_gb", period)

    def _meter_cap(base: int, addon: int) -> int | None:
        if base <= 0:
            return None  # unlimited
        return base + addon

    api_cap = _meter_cap(int(meter_defaults.get("api_calls") or 0), addon_api)
    ai_cap = _meter_cap(int(meter_defaults.get("ai_tokens") or 0), addon_ai)
    storage_cap = _meter_cap(int(meter_defaults.get("storage_gb") or 0), addon_storage)

    return {
        "tenant_id": tenant_id,
        "hotel_name": (hotel or {}).get("hotel_name"),
        "plan_code": plan_code,
        "period_ym": period,
        "rooms": rooms,
        "max_rooms": max_rooms,
        "addon_rooms": addon_rooms,
        "rooms_cap": rooms_cap,
        "rooms_over_limit": bool(rooms_cap is not None and rooms > rooms_cap),
        "staff": staff,
        "max_staff": max_staff,
        "addon_staff": addon_staff,
        "staff_cap": staff_cap,
        "staff_over_limit": bool(staff_cap is not None and staff > staff_cap),
        "api_calls": api_used,
        "api_calls_cap": api_cap,
        "api_calls_over_limit": bool(api_cap is not None and api_used > api_cap),
        "ai_tokens": ai_used,
        "ai_tokens_cap": ai_cap,
        "ai_tokens_over_limit": bool(ai_cap is not None and ai_used > ai_cap),
        "storage_gb": storage_used,
        "storage_gb_cap": storage_cap,
        "storage_gb_over_limit": bool(storage_cap is not None and storage_used > storage_cap),
    }


def _period_ym(dt: datetime | None = None) -> str:
    d = dt or datetime.now(timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


def _counter_value(conn: DbConn, tenant_id: str, metric: str, period_ym: str) -> float:
    ensure_growth_tables(conn)
    row = conn.execute(
        """SELECT value FROM webclaw_usage_counter
           WHERE tenant_id = ? AND metric = ? AND period_ym = ?""",
        (tenant_id, metric, period_ym),
    ).fetchone()
    if not row:
        return 0.0
    return float(_row(row, "value", 0) or 0)


def record_usage(tenant_id: str, metric: str, delta: float = 1.0) -> float:
    """Increment a usage counter for the current calendar month. Returns new value."""
    if metric not in ("api_calls", "ai_tokens", "storage_gb"):
        raise ValueError(f"Unsupported metric: {metric}")
    conn = db.get_connection()
    ensure_growth_tables(conn)
    period = _period_ym()
    now = _now()
    existing = conn.execute(
        """SELECT value FROM webclaw_usage_counter
           WHERE tenant_id = ? AND metric = ? AND period_ym = ?""",
        (tenant_id, metric, period),
    ).fetchone()
    if existing:
        new_val = float(_row(existing, "value", 0) or 0) + float(delta)
        conn.execute(
            """UPDATE webclaw_usage_counter SET value = ?, updated_at = ?
               WHERE tenant_id = ? AND metric = ? AND period_ym = ?""",
            (new_val, now, tenant_id, metric, period),
        )
    else:
        new_val = float(delta)
        conn.execute(
            """INSERT INTO webclaw_usage_counter
               (tenant_id, metric, period_ym, value, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (tenant_id, metric, period, new_val, now),
        )
    conn.commit()
    try:
        maybe_emit_usage_alerts(conn, tenant_id, metric)
    except Exception:
        pass
    return new_val


def maybe_emit_usage_alerts(conn: DbConn, tenant_id: str, metric: str) -> list[int]:
    """Emit 80%/100% threshold alerts once per period. Returns thresholds fired."""
    usage = _tenant_usage(conn, tenant_id)
    if metric == "api_calls":
        used, cap = usage.get("api_calls"), usage.get("api_calls_cap")
    elif metric == "ai_tokens":
        used, cap = usage.get("ai_tokens"), usage.get("ai_tokens_cap")
    else:
        used, cap = usage.get("storage_gb"), usage.get("storage_gb_cap")
    if cap is None or not cap:
        return []
    pct = (float(used or 0) / float(cap)) * 100.0
    fired: list[int] = []
    period = usage.get("period_ym") or _period_ym()
    for threshold in (80, 100):
        if pct < threshold:
            continue
        exists = conn.execute(
            """SELECT 1 FROM webclaw_usage_alert
               WHERE tenant_id = ? AND metric = ? AND period_ym = ? AND threshold_pct = ?""",
            (tenant_id, metric, period, threshold),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """INSERT INTO webclaw_usage_alert
               (id, tenant_id, metric, period_ym, threshold_pct, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), tenant_id, metric, period, threshold, _now()),
        )
        conn.commit()
        fired.append(threshold)
        try:
            from admin_ops import notify_ops

            notify_ops(
                "usage_threshold",
                {
                    "tenant_id": tenant_id,
                    "metric": metric,
                    "used": used,
                    "cap": cap,
                    "threshold_pct": threshold,
                },
            )
        except Exception:
            pass
    return fired


@router.get("/usage/alerts")
@require_any_permission("platform.billing.manage", "platform.tenants.view", "platform.admin")
async def list_usage_alerts(request: Request, limit: int = 50):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    limit = max(1, min(int(limit or 50), 200))
    rows = conn.execute(
        """SELECT id, tenant_id, metric, period_ym, threshold_pct, created_at
           FROM webclaw_usage_alert
           ORDER BY created_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return {
        "status": "ok",
        "alerts": [
            {
                "id": _row(r, "id", 0),
                "tenant_id": _row(r, "tenant_id", 1),
                "metric": _row(r, "metric", 2),
                "period_ym": _row(r, "period_ym", 3),
                "threshold_pct": int(_row(r, "threshold_pct", 4) or 0),
                "created_at": _row(r, "created_at", 5),
            }
            for r in rows
        ],
    }


def assert_within_plan_limits(
    tenant_id: str,
    metric: str,
    *,
    delta: float = 1,
) -> dict | None:
    """Return an error payload if adding ``delta`` units would exceed plan+addon caps.

    Unlimited plans (cap is None / <= 0) always pass. Returns None when allowed.
    """
    if metric not in ("rooms", "staff", "api_calls", "ai_tokens", "storage_gb"):
        return None
    if not tenant_id or delta <= 0:
        return None
    conn = db.get_connection()
    usage = _tenant_usage(conn, tenant_id)
    cap_map = {
        "rooms": ("rooms_cap", "rooms"),
        "staff": ("staff_cap", "staff"),
        "api_calls": ("api_calls_cap", "api_calls"),
        "ai_tokens": ("ai_tokens_cap", "ai_tokens"),
        "storage_gb": ("storage_gb_cap", "storage_gb"),
    }
    cap_key, cur_key = cap_map[metric]
    cap = usage.get(cap_key)
    current = float(usage.get(cur_key) or 0)
    if cap is None:
        return None
    cap_i = float(cap)
    if cap_i <= 0:
        return None
    if current + float(delta) > cap_i:
        return {
            "status": "error",
            "code": "PLAN_LIMIT_EXCEEDED",
            "metric": metric,
            "current": current,
            "cap": cap_i,
            "message": (
                f"Plan limit exceeded for {metric}: {current}/{cap_i}. "
                f"Upgrade the plan or assign a usage add-on."
            ),
        }
    return None


ROOM_CREATE_ACTIONS = frozenset(
    {
        "add-room",
        "hospitality-add-room",
        "create-room",
        "hospitality-create-room",
    }
)


# ── Feature flags ─────────────────────────────────────────────────────────────

class FlagUpdate(BaseModel):
    enabled_global: bool | None = None
    label: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class FlagTenantOverride(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=80)
    enabled: bool = True


@router.get("/feature-flags")
@require_any_permission("platform.settings.manage", "platform.admin")
async def list_feature_flags(request: Request):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    rows = conn.execute(
        """SELECT key, label, description, enabled_global, created_at, updated_at
           FROM webclaw_feature_flag ORDER BY key"""
    ).fetchall()
    flags = []
    for r in rows:
        key = _row(r, "key", 0)
        overrides = conn.execute(
            """SELECT tenant_id, enabled, updated_at
               FROM webclaw_feature_flag_tenant WHERE flag_key = ?""",
            (key,),
        ).fetchall()
        flags.append(
            {
                "key": key,
                "label": _row(r, "label", 1),
                "description": _row(r, "description", 2),
                "enabled_global": bool(_row(r, "enabled_global", 3)),
                "created_at": _row(r, "created_at", 4),
                "updated_at": _row(r, "updated_at", 5),
                "tenant_overrides": [
                    {
                        "tenant_id": _row(o, "tenant_id", 0),
                        "enabled": bool(_row(o, "enabled", 1)),
                        "updated_at": _row(o, "updated_at", 2),
                    }
                    for o in overrides
                ],
            }
        )
    return {"status": "ok", "flags": flags}


@router.put("/feature-flags/{flag_key}")
@require_any_permission("platform.settings.manage", "platform.admin")
async def update_feature_flag(flag_key: str, request: Request):
    try:
        body = await request.json()
        req = FlagUpdate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_growth_tables(conn)
    row = conn.execute(
        "SELECT key FROM webclaw_feature_flag WHERE key = ?", (flag_key,)
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "Flag not found"}, 404)
    current = conn.execute(
        "SELECT label, description, enabled_global FROM webclaw_feature_flag WHERE key = ?",
        (flag_key,),
    ).fetchone()
    label = req.label if req.label is not None else _row(current, "label", 0)
    description = (
        req.description if req.description is not None else _row(current, "description", 1)
    )
    enabled = (
        req.enabled_global
        if req.enabled_global is not None
        else bool(_row(current, "enabled_global", 2))
    )
    conn.execute(
        """UPDATE webclaw_feature_flag
           SET label=?, description=?, enabled_global=?, updated_at=?
           WHERE key=?""",
        (label, description, 1 if enabled else 0, _now(), flag_key),
    )
    conn.commit()
    _audit(conn, request, "feature_flag", "update_flag", flag_key)
    return {"status": "ok", "key": flag_key, "enabled_global": enabled}


@router.put("/feature-flags/{flag_key}/tenants")
@require_any_permission("platform.settings.manage", "platform.admin")
async def set_flag_tenant_override(flag_key: str, request: Request):
    try:
        body = await request.json()
        req = FlagTenantOverride(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_growth_tables(conn)
    if not conn.execute(
        "SELECT 1 FROM webclaw_feature_flag WHERE key = ?", (flag_key,)
    ).fetchone():
        return JSONResponse({"status": "error", "message": "Flag not found"}, 404)
    now = _now()
    conn.execute(
        """INSERT INTO webclaw_feature_flag_tenant (flag_key, tenant_id, enabled, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(flag_key, tenant_id) DO UPDATE SET
             enabled = excluded.enabled,
             updated_at = excluded.updated_at""",
        (flag_key, req.tenant_id, 1 if req.enabled else 0, now),
    )
    conn.commit()
    _audit(conn, request, "feature_flag", "set_tenant_override", f"{flag_key}:{req.tenant_id}")
    return {"status": "ok"}


@router.delete("/feature-flags/{flag_key}/tenants/{tenant_id}")
@require_any_permission("platform.settings.manage", "platform.admin")
async def clear_flag_tenant_override(flag_key: str, tenant_id: str, request: Request):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    conn.execute(
        "DELETE FROM webclaw_feature_flag_tenant WHERE flag_key = ? AND tenant_id = ?",
        (flag_key, tenant_id),
    )
    conn.commit()
    _audit(conn, request, "feature_flag", "clear_tenant_override", f"{flag_key}:{tenant_id}")
    return {"status": "ok"}


def get_effective_flags(conn: DbConn, tenant_id: str | None = None) -> dict[str, bool]:
    """Resolved flag map (tenant override wins over global)."""
    ensure_growth_tables(conn)
    rows = conn.execute(
        "SELECT key, enabled_global FROM webclaw_feature_flag"
    ).fetchall()
    out: dict[str, bool] = {}
    for r in rows:
        key = str(_row(r, "key", 0))
        enabled = bool(_row(r, "enabled_global", 1))
        if tenant_id:
            ov = conn.execute(
                """SELECT enabled FROM webclaw_feature_flag_tenant
                   WHERE flag_key = ? AND tenant_id = ?""",
                (key, tenant_id),
            ).fetchone()
            if ov is not None:
                enabled = bool(_row(ov, "enabled", 0))
        out[key] = enabled
    return out


def is_flag_enabled(tenant_id: str | None, flag_key: str) -> bool:
    """Return True when the flag is effectively ON for the tenant (or globally)."""
    conn = db.get_connection()
    flags = get_effective_flags(conn, tenant_id)
    return bool(flags.get(flag_key, False))


def is_kill_switch_on(tenant_id: str | None, flag_key: str) -> bool:
    """Kill switches: flag enabled means the feature is disabled."""
    return is_flag_enabled(tenant_id, flag_key)


@router.get("/feature-flags/effective/{tenant_id}")
@require_any_permission(
    "platform.settings.manage", "platform.tenants.view", "platform.admin"
)
async def effective_flags(tenant_id: str, request: Request):
    """Resolved flag map for a tenant (override wins over global)."""
    conn = db.get_connection()
    return {
        "status": "ok",
        "tenant_id": tenant_id,
        "flags": get_effective_flags(conn, tenant_id),
    }


# ── Usage & add-ons ───────────────────────────────────────────────────────────

@router.get("/usage/overview")
@require_any_permission("platform.billing.manage", "platform.tenants.view", "platform.admin")
async def usage_overview(request: Request):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    from admin_platform import _erp as erp_fn, _serialize_tenants_with_billing

    erp = erp_fn()
    tenants = []
    if erp:
        try:
            tenants = _serialize_tenants_with_billing(erp, conn)
        finally:
            erp.close()
    rows = []
    over_limit = 0
    for t in tenants:
        u = _tenant_usage(conn, str(t["id"]))
        if (
            u["rooms_over_limit"]
            or u["staff_over_limit"]
            or u.get("api_calls_over_limit")
            or u.get("ai_tokens_over_limit")
            or u.get("storage_gb_over_limit")
        ):
            over_limit += 1
        rows.append(u)
    addons = conn.execute(
        """SELECT id, code, name, metric, unit_price, included_units, is_active
           FROM webclaw_usage_addon ORDER BY metric, name"""
    ).fetchall()
    return {
        "status": "ok",
        "usage": rows,
        "over_limit_count": over_limit,
        "addons": [
            {
                "id": _row(a, "id", 0),
                "code": _row(a, "code", 1),
                "name": _row(a, "name", 2),
                "metric": _row(a, "metric", 3),
                "unit_price": _row(a, "unit_price", 4),
                "included_units": _row(a, "included_units", 5),
                "is_active": bool(_row(a, "is_active", 6)),
            }
            for a in addons
        ],
    }


@router.get("/usage/tenants/{tenant_id}")
@require_any_permission("platform.billing.manage", "platform.tenants.view", "platform.admin")
async def tenant_usage(tenant_id: str, request: Request):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    usage = _tenant_usage(conn, tenant_id)
    assigned = conn.execute(
        """SELECT ta.id, ta.quantity, a.code, a.name, a.metric, a.included_units, a.unit_price
           FROM webclaw_tenant_addon ta
           JOIN webclaw_usage_addon a ON a.id = ta.addon_id
           WHERE ta.tenant_id = ?""",
        (tenant_id,),
    ).fetchall()
    return {
        "status": "ok",
        "usage": usage,
        "assigned_addons": [
            {
                "id": _row(r, "id", 0),
                "quantity": _row(r, "quantity", 1),
                "code": _row(r, "code", 2),
                "name": _row(r, "name", 3),
                "metric": _row(r, "metric", 4),
                "included_units": _row(r, "included_units", 5),
                "unit_price": _row(r, "unit_price", 6),
            }
            for r in assigned
        ],
    }


class AssignAddonRequest(BaseModel):
    addon_id: str
    quantity: int = Field(default=1, ge=1, le=100)


@router.post("/usage/tenants/{tenant_id}/addons")
@require_any_permission("platform.billing.manage", "platform.admin")
async def assign_addon(tenant_id: str, request: Request):
    try:
        body = await request.json()
        req = AssignAddonRequest(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_growth_tables(conn)
    if not conn.execute(
        "SELECT 1 FROM webclaw_usage_addon WHERE id = ? AND is_active = 1", (req.addon_id,)
    ).fetchone():
        return JSONResponse({"status": "error", "message": "Add-on not found"}, 404)
    now = _now()
    existing = conn.execute(
        "SELECT id, quantity FROM webclaw_tenant_addon WHERE tenant_id = ? AND addon_id = ?",
        (tenant_id, req.addon_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE webclaw_tenant_addon SET quantity = ? WHERE id = ?",
            (req.quantity, _row(existing, "id", 0)),
        )
    else:
        conn.execute(
            """INSERT INTO webclaw_tenant_addon (id, tenant_id, addon_id, quantity, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), tenant_id, req.addon_id, req.quantity, now),
        )
    conn.commit()
    _audit(conn, request, "billing", "assign_addon", f"{tenant_id}:{req.addon_id}", tenant_id)
    return {"status": "ok", "usage": _tenant_usage(conn, tenant_id)}


@router.delete("/usage/tenants/{tenant_id}/addons/{assignment_id}")
@require_any_permission("platform.billing.manage", "platform.admin")
async def remove_addon(tenant_id: str, assignment_id: str, request: Request):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    conn.execute(
        "DELETE FROM webclaw_tenant_addon WHERE id = ? AND tenant_id = ?",
        (assignment_id, tenant_id),
    )
    conn.commit()
    _audit(conn, request, "billing", "remove_addon", assignment_id, tenant_id)
    return {"status": "ok"}


# ── White-label ───────────────────────────────────────────────────────────────

class BrandingUpdate(BaseModel):
    brand_name: str | None = Field(default=None, max_length=120)
    logo_url: str | None = Field(default=None, max_length=500)
    primary_color: str | None = Field(default=None, max_length=20)
    custom_domain: str | None = Field(default=None, max_length=200)
    email_from_name: str | None = Field(default=None, max_length=120)
    email_from_address: str | None = Field(default=None, max_length=200)
    support_url: str | None = Field(default=None, max_length=500)


@router.get("/white-label")
@require_any_permission("platform.settings.manage", "platform.tenants.view", "platform.admin")
async def list_white_label(request: Request):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    rows = conn.execute(
        """SELECT * FROM webclaw_tenant_branding ORDER BY updated_at DESC"""
    ).fetchall()
    # Enrich with hotel names
    from admin_platform import _erp as erp_fn, _serialize_tenants_with_billing

    name_by_id: dict[str, str] = {}
    erp = erp_fn()
    if erp:
        try:
            for t in _serialize_tenants_with_billing(erp, conn):
                name_by_id[str(t["id"])] = t.get("hotel_name") or ""
        finally:
            erp.close()
    items = []
    for r in rows:
        tid = str(_row(r, "tenant_id", 0))
        item = _branding_dict(tid, r)
        item["hotel_name"] = name_by_id.get(tid)
        items.append(item)
    return {"status": "ok", "brands": items}


def _branding_dict(tenant_id: str, r=None) -> dict:
    if not r:
        return {
            "tenant_id": tenant_id,
            "brand_name": None,
            "logo_url": None,
            "primary_color": None,
            "custom_domain": None,
            "email_from_name": None,
            "email_from_address": None,
            "support_url": None,
            "dns_status": None,
            "dns_verified": False,
            "dns_checked_at": None,
            "dns_target": None,
        }
    return {
        "tenant_id": tenant_id,
        "brand_name": _row(r, "brand_name", 1),
        "logo_url": _row(r, "logo_url", 2),
        "primary_color": _row(r, "primary_color", 3),
        "custom_domain": _row(r, "custom_domain", 4),
        "email_from_name": _row(r, "email_from_name", 5),
        "email_from_address": _row(r, "email_from_address", 6),
        "support_url": _row(r, "support_url", 7),
        "updated_at": _row(r, "updated_at", 8),
        "dns_status": _row(r, "dns_status", 9),
        "dns_verified": bool(_row(r, "dns_verified", 10) or 0),
        "dns_checked_at": _row(r, "dns_checked_at", 11),
        "dns_target": _row(r, "dns_target", 12),
    }


def resolve_branding_by_host(host: str) -> dict | None:
    """Runtime branding lookup by custom domain (Host header)."""
    host = (host or "").strip().lower().split(":")[0]
    if not host:
        return None
    conn = db.get_connection()
    ensure_growth_tables(conn)
    r = conn.execute(
        """SELECT * FROM webclaw_tenant_branding
           WHERE lower(custom_domain) = ? OR lower(custom_domain) = ?""",
        (host, f"www.{host}" if not host.startswith("www.") else host[4:]),
    ).fetchone()
    if not r:
        # also match without www
        r = conn.execute(
            """SELECT * FROM webclaw_tenant_branding
               WHERE lower(replace(custom_domain, 'www.', '')) = ?""",
            (host.replace("www.", ""),),
        ).fetchone()
    if not r:
        return None
    tid = str(_row(r, "tenant_id", 0))
    return _branding_dict(tid, r)


def get_tenant_branding(tenant_id: str) -> dict | None:
    conn = db.get_connection()
    ensure_growth_tables(conn)
    r = conn.execute(
        "SELECT * FROM webclaw_tenant_branding WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    if not r:
        return None
    return _branding_dict(tenant_id, r)


@router.get("/white-label/{tenant_id}")
@require_any_permission("platform.settings.manage", "platform.tenants.view", "platform.admin")
async def get_white_label(tenant_id: str, request: Request):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    r = conn.execute(
        "SELECT * FROM webclaw_tenant_branding WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    return {"status": "ok", "branding": _branding_dict(tenant_id, r)}


@router.put("/white-label/{tenant_id}")
@require_any_permission("platform.settings.manage", "platform.admin")
async def upsert_white_label(tenant_id: str, request: Request):
    try:
        body = await request.json()
        req = BrandingUpdate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_growth_tables(conn)
    now = _now()
    conn.execute(
        """INSERT INTO webclaw_tenant_branding
           (tenant_id, brand_name, logo_url, primary_color, custom_domain,
            email_from_name, email_from_address, support_url, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(tenant_id) DO UPDATE SET
             brand_name = excluded.brand_name,
             logo_url = excluded.logo_url,
             primary_color = excluded.primary_color,
             custom_domain = excluded.custom_domain,
             email_from_name = excluded.email_from_name,
             email_from_address = excluded.email_from_address,
             support_url = excluded.support_url,
             updated_at = excluded.updated_at""",
        (
            tenant_id,
            req.brand_name,
            req.logo_url,
            req.primary_color,
            req.custom_domain,
            req.email_from_name,
            req.email_from_address,
            req.support_url,
            now,
        ),
    )
    # Reset DNS verification when domain changes
    conn.execute(
        """UPDATE webclaw_tenant_branding
           SET dns_verified = 0, dns_status = 'pending', dns_checked_at = NULL
           WHERE tenant_id = ? AND custom_domain IS NOT NULL AND custom_domain != ''""",
        (tenant_id,),
    )
    conn.commit()
    _audit(conn, request, "white_label", "upsert_branding", tenant_id, tenant_id)
    return await get_white_label(tenant_id, request)


@router.post("/white-label/{tenant_id}/verify-dns")
@require_any_permission("platform.settings.manage", "platform.admin")
async def verify_white_label_dns(tenant_id: str, request: Request):
    """Check that custom_domain CNAME/A points at the platform target host."""
    import socket

    conn = db.get_connection()
    ensure_growth_tables(conn)
    r = conn.execute(
        "SELECT custom_domain FROM webclaw_tenant_branding WHERE tenant_id = ?",
        (tenant_id,),
    ).fetchone()
    domain = (_row(r, "custom_domain", 0) if r else None) or ""
    domain = str(domain).strip().lower()
    if not domain:
        return JSONResponse(
            {"status": "error", "message": "No custom_domain configured"}, 400
        )
    import os

    target = (
        os.environ.get("WEBCLAW_BRANDING_DNS_TARGET")
        or os.environ.get("WEBCLAW_PUBLIC_HOST")
        or "app.hotelpilot.io"
    ).strip().lower()
    status = "failed"
    detail = ""
    verified = False
    try:
        answers = socket.getaddrinfo(domain, None)
        resolved = sorted({a[4][0] for a in answers if a and a[4]})
        try:
            target_ips = sorted({a[4][0] for a in socket.getaddrinfo(target, None) if a and a[4]})
        except Exception:
            target_ips = []
        if target_ips and set(resolved) & set(target_ips):
            status = "verified"
            verified = True
            detail = f"Resolved {domain} → {', '.join(resolved[:4])} matches {target}"
        else:
            # Accept CNAME-style textual match via gethostbyname_ex
            try:
                canon, aliases, ips = socket.gethostbyname_ex(domain)
                if target in (canon or "").lower() or any(target in (a or "").lower() for a in aliases):
                    status = "verified"
                    verified = True
                    detail = f"CNAME/alias points at {target}"
                else:
                    status = "pending"
                    detail = (
                        f"Resolved {domain} → {', '.join(ips[:4] or resolved[:4])}; "
                        f"expected host {target}. Point a CNAME to {target}."
                    )
            except Exception:
                status = "pending"
                detail = f"Point {domain} CNAME to {target}. Current IPs: {', '.join(resolved[:4]) or 'none'}"
    except Exception as e:
        status = "failed"
        detail = f"DNS lookup failed: {e}"

    now = _now()
    conn.execute(
        """UPDATE webclaw_tenant_branding
           SET dns_status = ?, dns_verified = ?, dns_checked_at = ?, dns_target = ?
           WHERE tenant_id = ?""",
        (status, 1 if verified else 0, now, target, tenant_id),
    )
    conn.commit()
    _audit(conn, request, "white_label", "verify_dns", f"{tenant_id}:{status}", tenant_id)
    return {
        "status": "ok",
        "dns": {
            "domain": domain,
            "target": target,
            "dns_status": status,
            "dns_verified": verified,
            "dns_checked_at": now,
            "detail": detail,
        },
        "branding": (await get_white_label(tenant_id, request))["branding"],
    }


@router.get("/public/branding")
async def public_branding(request: Request, host: str | None = None, tenant_id: str | None = None):
    """Unauthenticated runtime branding for login / guest shells."""
    branding = None
    if tenant_id:
        branding = get_tenant_branding(tenant_id)
    if not branding:
        hdr = host or request.headers.get("x-forwarded-host") or request.headers.get("host")
        branding = resolve_branding_by_host(hdr or "")
    if not branding:
        return {"status": "ok", "branding": None}
    # Public surface — omit nothing sensitive (no secrets stored here)
    return {
        "status": "ok",
        "branding": {
            "tenant_id": branding.get("tenant_id"),
            "brand_name": branding.get("brand_name"),
            "logo_url": branding.get("logo_url"),
            "primary_color": branding.get("primary_color"),
            "custom_domain": branding.get("custom_domain"),
            "support_url": branding.get("support_url"),
            "dns_verified": branding.get("dns_verified"),
        },
    }


# ── SSO / SCIM ────────────────────────────────────────────────────────────────

class IdentityUpdate(BaseModel):
    sso_enabled: bool | None = None
    sso_protocol: str | None = None
    issuer_url: str | None = Field(default=None, max_length=500)
    client_id: str | None = Field(default=None, max_length=200)
    client_secret: str | None = Field(default=None, max_length=500)
    metadata_url: str | None = Field(default=None, max_length=500)
    scim_enabled: bool | None = None

    @field_validator("sso_protocol")
    @classmethod
    def proto_ok(cls, v: str | None) -> str | None:
        if v is None:
            return v
        value = v.lower()
        if value not in {"oidc", "saml"}:
            raise ValueError("sso_protocol must be oidc or saml")
        return value


def _mask(secret: str | None) -> str:
    if not secret:
        return ""
    if len(secret) <= 4:
        return "••••"
    return "•" * (len(secret) - 4) + secret[-4:]


@router.get("/identity")
@require_any_permission("platform.security.manage", "platform.tenants.view", "platform.admin")
async def list_identity(request: Request):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    rows = conn.execute(
        """SELECT tenant_id, sso_enabled, sso_protocol, issuer_url, client_id,
                  metadata_url, scim_enabled, updated_at
           FROM webclaw_tenant_identity ORDER BY updated_at DESC"""
    ).fetchall()
    return {
        "status": "ok",
        "configs": [
            {
                "tenant_id": _row(r, "tenant_id", 0),
                "sso_enabled": bool(_row(r, "sso_enabled", 1)),
                "sso_protocol": _row(r, "sso_protocol", 2),
                "issuer_url": _row(r, "issuer_url", 3),
                "client_id": _row(r, "client_id", 4),
                "metadata_url": _row(r, "metadata_url", 5),
                "scim_enabled": bool(_row(r, "scim_enabled", 6)),
                "updated_at": _row(r, "updated_at", 7),
            }
            for r in rows
        ],
    }


def _scim_base_url(tenant_id: str) -> str:
    import os

    base = (
        os.environ.get("WEBCLAW_API_PUBLIC_URL")
        or os.environ.get("WEBCLAW_PUBLIC_API_URL")
        or ""
    ).rstrip("/")
    path = f"/api/v1/scim/v2/{tenant_id}"
    return f"{base}{path}" if base else path


def _identity_payload(tenant_id: str, r=None, *, reveal_scim: str | None = None) -> dict:
    if not r:
        return {
            "tenant_id": tenant_id,
            "sso_enabled": False,
            "sso_protocol": "oidc",
            "issuer_url": "",
            "client_id": "",
            "client_secret": "",
            "metadata_url": "",
            "scim_enabled": False,
            "scim_bearer_token": "",
            "scim_base_url": _scim_base_url(tenant_id),
            "sso_start_url": f"/api/v1/auth/sso/{tenant_id}/start",
        }
    token = reveal_scim if reveal_scim is not None else _mask(_row(r, "scim_bearer_token", 8))
    return {
        "tenant_id": tenant_id,
        "sso_enabled": bool(_row(r, "sso_enabled", 1)),
        "sso_protocol": _row(r, "sso_protocol", 2) or "oidc",
        "issuer_url": _row(r, "issuer_url", 3) or "",
        "client_id": _row(r, "client_id", 4) or "",
        "client_secret": _mask(_row(r, "client_secret", 5)),
        "metadata_url": _row(r, "metadata_url", 6) or "",
        "scim_enabled": bool(_row(r, "scim_enabled", 7)),
        "scim_bearer_token": token or "",
        "scim_base_url": _scim_base_url(tenant_id),
        "sso_start_url": f"/api/v1/auth/sso/{tenant_id}/start",
        "updated_at": _row(r, "updated_at", 9),
        "scim_token_revealed": reveal_scim is not None,
    }


@router.get("/identity/{tenant_id}")
@require_any_permission("platform.security.manage", "platform.tenants.view", "platform.admin")
async def get_identity(tenant_id: str, request: Request):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    r = conn.execute(
        "SELECT * FROM webclaw_tenant_identity WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    return {"status": "ok", "identity": _identity_payload(tenant_id, r)}


@router.put("/identity/{tenant_id}")
@require_any_permission("platform.security.manage", "platform.admin")
async def upsert_identity(tenant_id: str, request: Request):
    try:
        body = await request.json()
        req = IdentityUpdate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_growth_tables(conn)
    existing = conn.execute(
        "SELECT * FROM webclaw_tenant_identity WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    now = _now()
    sso_enabled = (
        req.sso_enabled
        if req.sso_enabled is not None
        else (bool(_row(existing, "sso_enabled", 1)) if existing else False)
    )
    protocol = req.sso_protocol or (_row(existing, "sso_protocol", 2) if existing else "oidc")
    issuer = req.issuer_url if req.issuer_url is not None else (_row(existing, "issuer_url", 3) if existing else "")
    client_id = req.client_id if req.client_id is not None else (_row(existing, "client_id", 4) if existing else "")
    secret = req.client_secret
    if secret is None or secret.startswith("•"):
        secret = _row(existing, "client_secret", 5) if existing else ""
    metadata = req.metadata_url if req.metadata_url is not None else (_row(existing, "metadata_url", 6) if existing else "")
    scim = (
        req.scim_enabled
        if req.scim_enabled is not None
        else (bool(_row(existing, "scim_enabled", 7)) if existing else False)
    )
    scim_token = _row(existing, "scim_bearer_token", 8) if existing else None
    revealed: str | None = None
    if scim and not scim_token:
        scim_token = uuid.uuid4().hex + uuid.uuid4().hex
        revealed = scim_token
    if not scim:
        scim_token = scim_token or ""

    conn.execute(
        """INSERT INTO webclaw_tenant_identity
           (tenant_id, sso_enabled, sso_protocol, issuer_url, client_id, client_secret,
            metadata_url, scim_enabled, scim_bearer_token, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(tenant_id) DO UPDATE SET
             sso_enabled = excluded.sso_enabled,
             sso_protocol = excluded.sso_protocol,
             issuer_url = excluded.issuer_url,
             client_id = excluded.client_id,
             client_secret = excluded.client_secret,
             metadata_url = excluded.metadata_url,
             scim_enabled = excluded.scim_enabled,
             scim_bearer_token = excluded.scim_bearer_token,
             updated_at = excluded.updated_at""",
        (
            tenant_id,
            1 if sso_enabled else 0,
            protocol,
            issuer,
            client_id,
            secret,
            metadata,
            1 if scim else 0,
            scim_token,
            now,
        ),
    )
    conn.commit()
    _audit(conn, request, "identity", "upsert_sso", tenant_id, tenant_id)
    r = conn.execute(
        "SELECT * FROM webclaw_tenant_identity WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    return {"status": "ok", "identity": _identity_payload(tenant_id, r, reveal_scim=revealed)}


@router.post("/identity/{tenant_id}/regenerate-scim-token")
@require_any_permission("platform.security.manage", "platform.admin")
async def regenerate_scim_token(tenant_id: str, request: Request):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    existing = conn.execute(
        "SELECT * FROM webclaw_tenant_identity WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    if not existing or not bool(_row(existing, "scim_enabled", 7)):
        return JSONResponse(
            {"status": "error", "message": "Enable SCIM before regenerating a token"},
            400,
        )
    token = uuid.uuid4().hex + uuid.uuid4().hex
    now = _now()
    conn.execute(
        """UPDATE webclaw_tenant_identity
           SET scim_bearer_token = ?, updated_at = ? WHERE tenant_id = ?""",
        (token, now, tenant_id),
    )
    conn.commit()
    _audit(conn, request, "identity", "regenerate_scim_token", tenant_id, tenant_id)
    r = conn.execute(
        "SELECT * FROM webclaw_tenant_identity WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    return {"status": "ok", "identity": _identity_payload(tenant_id, r, reveal_scim=token)}


# ── Compliance / DSAR ─────────────────────────────────────────────────────────

class CompliancePolicyUpdate(BaseModel):
    retention_days: int | None = Field(default=None, ge=30, le=3650)
    auto_purge_cancelled: bool | None = None
    dpa_contact_email: str | None = Field(default=None, max_length=200)


class DsarCreate(BaseModel):
    tenant_id: str
    request_type: str
    subject_email: str = Field(min_length=3, max_length=200)
    notes: str = Field(default="", max_length=4000)
    hotel_name: str | None = None

    @field_validator("request_type")
    @classmethod
    def type_ok(cls, v: str) -> str:
        value = v.lower()
        if value not in {"export", "erasure"}:
            raise ValueError("request_type must be export or erasure")
        return value


class DsarUpdate(BaseModel):
    status: str | None = None
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("status")
    @classmethod
    def status_ok(cls, v: str | None) -> str | None:
        if v is None:
            return v
        value = v.lower()
        if value not in {"open", "in_progress", "completed", "rejected"}:
            raise ValueError("invalid status")
        return value


@router.get("/compliance/policy")
@require_any_permission("platform.security.manage", "platform.admin")
async def get_compliance_policy(request: Request):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    r = conn.execute(
        "SELECT retention_days, auto_purge_cancelled, dpa_contact_email, updated_at FROM webclaw_compliance_policy WHERE id = 'default'"
    ).fetchone()
    return {
        "status": "ok",
        "policy": {
            "retention_days": int(_row(r, "retention_days", 0) or 365),
            "auto_purge_cancelled": bool(_row(r, "auto_purge_cancelled", 1)),
            "dpa_contact_email": _row(r, "dpa_contact_email", 2) or "",
            "updated_at": _row(r, "updated_at", 3),
        },
    }


@router.put("/compliance/policy")
@require_any_permission("platform.security.manage", "platform.admin")
async def update_compliance_policy(request: Request):
    try:
        body = await request.json()
        req = CompliancePolicyUpdate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_growth_tables(conn)
    current = (await get_compliance_policy(request))["policy"]
    patch = req.model_dump(exclude_none=True)
    current.update(patch)
    uid, email = _actor(request)
    conn.execute(
        """INSERT INTO webclaw_compliance_policy
           (id, retention_days, auto_purge_cancelled, dpa_contact_email, updated_at, updated_by)
           VALUES ('default', ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             retention_days = excluded.retention_days,
             auto_purge_cancelled = excluded.auto_purge_cancelled,
             dpa_contact_email = excluded.dpa_contact_email,
             updated_at = excluded.updated_at,
             updated_by = excluded.updated_by""",
        (
            int(current["retention_days"]),
            1 if current["auto_purge_cancelled"] else 0,
            current.get("dpa_contact_email") or "",
            _now(),
            email or uid,
        ),
    )
    conn.commit()
    _audit(conn, request, "compliance", "update_policy", json.dumps(current))
    return {"status": "ok", "policy": current}


@router.get("/compliance/dsar")
@require_any_permission("platform.security.manage", "platform.audit.view", "platform.admin")
async def list_dsar(request: Request, status: str | None = None):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    if status and status != "all":
        rows = conn.execute(
            """SELECT * FROM webclaw_dsar_request WHERE status = ?
               ORDER BY updated_at DESC LIMIT 200""",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM webclaw_dsar_request ORDER BY updated_at DESC LIMIT 200"
        ).fetchall()
    return {
        "status": "ok",
        "requests": [
            {
                "id": _row(r, "id", 0),
                "tenant_id": _row(r, "tenant_id", 1),
                "hotel_name": _row(r, "hotel_name", 2),
                "request_type": _row(r, "request_type", 3),
                "subject_email": _row(r, "subject_email", 4),
                "status": _row(r, "status", 5),
                "notes": _row(r, "notes", 6),
                "created_by_email": _row(r, "created_by_email", 8),
                "created_at": _row(r, "created_at", 9),
                "updated_at": _row(r, "updated_at", 10),
                "completed_at": _row(r, "completed_at", 11),
            }
            for r in rows
        ],
    }


@router.post("/compliance/dsar")
@require_any_permission("platform.security.manage", "platform.admin")
async def create_dsar(request: Request):
    try:
        body = await request.json()
        req = DsarCreate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_growth_tables(conn)
    uid, email = _actor(request)
    now = _now()
    rid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO webclaw_dsar_request
           (id, tenant_id, hotel_name, request_type, subject_email, status, notes,
            created_by, created_by_email, created_at, updated_at, completed_at)
           VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, NULL)""",
        (
            rid,
            req.tenant_id,
            req.hotel_name,
            req.request_type,
            req.subject_email.strip().lower(),
            req.notes,
            uid,
            email,
            now,
            now,
        ),
    )
    conn.commit()
    _audit(conn, request, "compliance", "dsar_created", rid, req.tenant_id)
    return {"status": "ok", "id": rid}


@router.patch("/compliance/dsar/{request_id}")
@require_any_permission("platform.security.manage", "platform.admin")
async def update_dsar(request_id: str, request: Request):
    try:
        body = await request.json()
        req = DsarUpdate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_growth_tables(conn)
    row = conn.execute(
        "SELECT id, status FROM webclaw_dsar_request WHERE id = ?", (request_id,)
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "DSAR not found"}, 404)
    now = _now()
    status = req.status
    notes = req.notes
    sets = ["updated_at = ?"]
    params: list = [now]
    if status is not None:
        sets.append("status = ?")
        params.append(status)
        if status in ("completed", "rejected"):
            sets.append("completed_at = ?")
            params.append(now)
    if notes is not None:
        sets.append("notes = ?")
        params.append(notes)
    params.append(request_id)
    conn.execute(
        f"UPDATE webclaw_dsar_request SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    conn.commit()
    _audit(conn, request, "compliance", "dsar_updated", request_id)
    return {"status": "ok"}


def build_dsar_export_package(tenant_id: str, subject_email: str) -> dict:
    """Full subject-scoped export across platform + hotel operational tables."""
    subject = (subject_email or "").strip().lower()
    conn = db.get_connection()
    ensure_growth_tables(conn)
    package: dict = {
        "tenant_id": tenant_id,
        "subject_email": subject,
        "generated_at": _now(),
        "users": [],
        "sessions": [],
        "support_tickets": [],
        "guests": [],
        "reservations": [],
        "audit_events": [],
    }
    cols = table_columns(conn, "webclaw_user") or []
    user_ids: list[str] = []
    if "email" in cols:
        sql = "SELECT id, email, full_name, status, created_at FROM webclaw_user WHERE lower(email) = ?"
        params: list = [subject]
        if "tenant_id" in cols:
            sql += " AND (tenant_id = ? OR tenant_id IS NULL)"
            params.append(tenant_id)
        for u in conn.execute(sql, params).fetchall():
            uid = str(_row(u, "id", 0))
            user_ids.append(uid)
            package["users"].append(
                {
                    "id": uid,
                    "email": _row(u, "email", 1),
                    "full_name": _row(u, "full_name", 2),
                    "status": _row(u, "status", 3),
                    "created_at": _row(u, "created_at", 4),
                }
            )
    for uid in user_ids:
        try:
            for s in conn.execute(
                """SELECT id, ip_address, user_agent, expires_at, created_at
                   FROM webclaw_session WHERE user_id = ? LIMIT 50""",
                (uid,),
            ).fetchall():
                package["sessions"].append(
                    {
                        "id": _row(s, "id", 0),
                        "ip_address": _row(s, "ip_address", 1),
                        "user_agent": _row(s, "user_agent", 2),
                        "expires_at": _row(s, "expires_at", 3),
                        "created_at": _row(s, "created_at", 4),
                    }
                )
        except Exception:
            pass
    try:
        for t in conn.execute(
            """SELECT id, subject, status, priority, created_at, requester_email
               FROM webclaw_support_ticket
               WHERE tenant_id = ? AND lower(requester_email) = ?
               LIMIT 100""",
            (tenant_id, subject),
        ).fetchall():
            package["support_tickets"].append(
                {
                    "id": _row(t, "id", 0),
                    "subject": _row(t, "subject", 1),
                    "status": _row(t, "status", 2),
                    "priority": _row(t, "priority", 3),
                    "created_at": _row(t, "created_at", 4),
                }
            )
    except Exception:
        pass
    try:
        for a in conn.execute(
            """SELECT id, timestamp, action_category, action, detail
               FROM webclaw_platform_audit
               WHERE tenant_id = ? AND (
                 lower(user_email) = ? OR lower(detail) LIKE ?
               )
               ORDER BY timestamp DESC LIMIT 100""",
            (tenant_id, subject, f"%{subject}%"),
        ).fetchall():
            package["audit_events"].append(
                {
                    "id": _row(a, "id", 0),
                    "timestamp": _row(a, "timestamp", 1),
                    "action_category": _row(a, "action_category", 2),
                    "action": _row(a, "action", 3),
                    "detail": _row(a, "detail", 4),
                }
            )
    except Exception:
        pass

    erp = _erp()
    if erp:
        try:
            # Guests matching email
            try:
                for g in erp.execute(
                    """SELECT id, first_name, last_name, email, phone, created_at
                       FROM hospitalityclaw_guest
                       WHERE tenant_id = ? AND lower(email) = ?
                       LIMIT 200""",
                    (tenant_id, subject),
                ).fetchall():
                    package["guests"].append(
                        {
                            "id": _row(g, "id", 0),
                            "first_name": _row(g, "first_name", 1),
                            "last_name": _row(g, "last_name", 2),
                            "email": _row(g, "email", 3),
                            "phone": _row(g, "phone", 4),
                            "created_at": _row(g, "created_at", 5),
                        }
                    )
            except Exception:
                pass
            try:
                for r in erp.execute(
                    """SELECT id, confirmation_code, status, check_in, check_out,
                              guest_name, guest_email
                       FROM hospitalityclaw_reservation
                       WHERE tenant_id = ? AND lower(guest_email) = ?
                       ORDER BY check_in DESC LIMIT 200""",
                    (tenant_id, subject),
                ).fetchall():
                    package["reservations"].append(
                        {
                            "id": _row(r, "id", 0),
                            "confirmation_code": _row(r, "confirmation_code", 1),
                            "status": _row(r, "status", 2),
                            "check_in": _row(r, "check_in", 3),
                            "check_out": _row(r, "check_out", 4),
                            "guest_name": _row(r, "guest_name", 5),
                            "guest_email": _row(r, "guest_email", 6),
                        }
                    )
            except Exception:
                pass
        finally:
            erp.close()
    return package


def execute_dsar_erasure(tenant_id: str, subject_email: str) -> dict:
    """Anonymize / delete subject PII across platform + hotel tables."""
    subject = (subject_email or "").strip().lower()
    conn = db.get_connection()
    ensure_growth_tables(conn)
    summary = {
        "users_disabled": 0,
        "sessions_revoked": 0,
        "guests_anonymized": 0,
        "reservations_anonymized": 0,
    }
    cols = table_columns(conn, "webclaw_user") or []
    user_ids: list[str] = []
    if "email" in cols:
        sql = "SELECT id FROM webclaw_user WHERE lower(email) = ?"
        params: list = [subject]
        if "tenant_id" in cols:
            sql += " AND (tenant_id = ? OR tenant_id IS NULL)"
            params.append(tenant_id)
        for u in conn.execute(sql, params).fetchall():
            user_ids.append(str(_row(u, "id", 0)))
    for uid in user_ids:
        anon = f"erased+{uid[:8]}@invalid.local"
        conn.execute(
            """UPDATE webclaw_user
               SET email = ?, full_name = 'ERASED', status = 'disabled',
                   password_hash = '', failed_login_attempts = 0, locked_until = NULL
               WHERE id = ?""",
            (anon, uid),
        )
        summary["users_disabled"] += 1
        cur = conn.execute("DELETE FROM webclaw_session WHERE user_id = ?", (uid,))
        summary["sessions_revoked"] += int(cur.rowcount or 0) if cur is not None else 0
    conn.commit()

    erp = _erp()
    if erp:
        try:
            try:
                cur = erp.execute(
                    """UPDATE hospitalityclaw_guest
                       SET first_name = 'ERASED', last_name = 'ERASED',
                           email = ?, phone = NULL
                       WHERE tenant_id = ? AND lower(email) = ?""",
                    (f"erased@{tenant_id[:8]}.invalid", tenant_id, subject),
                )
                summary["guests_anonymized"] = int(cur.rowcount or 0) if cur is not None else 0
            except Exception:
                pass
            try:
                cur = erp.execute(
                    """UPDATE hospitalityclaw_reservation
                       SET guest_name = 'ERASED', guest_email = ?
                       WHERE tenant_id = ? AND lower(guest_email) = ?""",
                    (f"erased@{tenant_id[:8]}.invalid", tenant_id, subject),
                )
                summary["reservations_anonymized"] = (
                    int(cur.rowcount or 0) if cur is not None else 0
                )
            except Exception:
                pass
            erp.commit()
        finally:
            erp.close()
    return summary


@router.get("/compliance/dsar/{request_id}/export")
@require_any_permission("platform.security.manage", "platform.admin")
async def export_dsar_package(request_id: str, request: Request):
    """Produce a subject-scoped JSON export package for a DSAR export request."""
    conn = db.get_connection()
    ensure_growth_tables(conn)
    row = conn.execute(
        "SELECT * FROM webclaw_dsar_request WHERE id = ?", (request_id,)
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "DSAR not found"}, 404)
    req_type = _row(row, "request_type", 3)
    if req_type != "export":
        return JSONResponse(
            {"status": "error", "message": "Export package only for export requests"},
            400,
        )
    tenant_id = str(_row(row, "tenant_id", 1))
    subject = str(_row(row, "subject_email", 4) or "").lower()
    package = build_dsar_export_package(tenant_id, subject)
    package["request_id"] = request_id
    payload = json.dumps(package, indent=2, default=str).encode("utf-8")
    _audit(conn, request, "compliance", "dsar_export_downloaded", request_id, tenant_id)
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="dsar-{request_id[:8]}.json"'
        },
    )


@router.post("/compliance/dsar/{request_id}/execute-erasure")
@require_any_permission("platform.security.manage", "platform.admin")
async def execute_dsar_erasure_route(request_id: str, request: Request):
    """Run erasure for a DSAR erasure request and mark it completed."""
    conn = db.get_connection()
    ensure_growth_tables(conn)
    row = conn.execute(
        "SELECT * FROM webclaw_dsar_request WHERE id = ?", (request_id,)
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "DSAR not found"}, 404)
    if _row(row, "request_type", 3) != "erasure":
        return JSONResponse(
            {"status": "error", "message": "Execute erasure only for erasure requests"},
            400,
        )
    if _row(row, "status", 5) in ("completed", "rejected"):
        return JSONResponse(
            {"status": "error", "message": "Request already closed"}, 400
        )
    tenant_id = str(_row(row, "tenant_id", 1))
    subject = str(_row(row, "subject_email", 4) or "")
    summary = execute_dsar_erasure(tenant_id, subject)
    now = _now()
    note = json.dumps(summary)
    prev = str(_row(row, "notes", 6) or "")
    merged = f"{prev}\n{note}".strip() if prev else note
    conn.execute(
        """UPDATE webclaw_dsar_request
           SET status = 'completed', completed_at = ?, updated_at = ?, notes = ?
           WHERE id = ?""",
        (now, now, merged, request_id),
    )
    conn.commit()
    _audit(
        conn,
        request,
        "compliance",
        "dsar_erasure_executed",
        request_id,
        tenant_id,
    )
    return {"status": "ok", "summary": summary}


# ── Partners ──────────────────────────────────────────────────────────────────

class PartnerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    contact_email: str = Field(min_length=3, max_length=200)
    commission_percent: float = Field(default=0, ge=0, le=100)
    notes: str = Field(default="", max_length=2000)


class PartnerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    contact_email: str | None = Field(default=None, max_length=200)
    commission_percent: float | None = Field(default=None, ge=0, le=100)
    status: str | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("status")
    @classmethod
    def status_ok(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in {"active", "inactive"}:
            raise ValueError("status must be active or inactive")
        return v


@router.get("/partners")
@require_any_permission("platform.billing.manage", "platform.tenants.view", "platform.admin")
async def list_partners(request: Request):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    rows = conn.execute(
        """SELECT id, name, contact_email, commission_percent, status, notes, created_at, updated_at
           FROM webclaw_partner ORDER BY name"""
    ).fetchall()
    out = []
    for r in rows:
        pid = _row(r, "id", 0)
        links = conn.execute(
            "SELECT tenant_id FROM webclaw_partner_tenant WHERE partner_id = ?",
            (pid,),
        ).fetchall()
        out.append(
            {
                "id": pid,
                "name": _row(r, "name", 1),
                "contact_email": _row(r, "contact_email", 2),
                "commission_percent": float(_row(r, "commission_percent", 3) or 0),
                "status": _row(r, "status", 4),
                "notes": _row(r, "notes", 5),
                "created_at": _row(r, "created_at", 6),
                "updated_at": _row(r, "updated_at", 7),
                "tenant_ids": [_row(l, "tenant_id", 0) for l in links],
                "tenant_count": len(links),
            }
        )
    return {"status": "ok", "partners": out}


@router.post("/partners")
@require_any_permission("platform.billing.manage", "platform.admin")
async def create_partner(request: Request):
    try:
        body = await request.json()
        req = PartnerCreate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_growth_tables(conn)
    now = _now()
    pid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO webclaw_partner
           (id, name, contact_email, commission_percent, status, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'active', ?, ?, ?)""",
        (
            pid,
            req.name.strip(),
            req.contact_email.strip().lower(),
            float(req.commission_percent),
            req.notes,
            now,
            now,
        ),
    )
    conn.commit()
    _audit(conn, request, "partner", "create_partner", pid)
    return {"status": "ok", "id": pid}


@router.patch("/partners/{partner_id}")
@require_any_permission("platform.billing.manage", "platform.admin")
async def update_partner(partner_id: str, request: Request):
    try:
        body = await request.json()
        req = PartnerUpdate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_growth_tables(conn)
    if not conn.execute(
        "SELECT 1 FROM webclaw_partner WHERE id = ?", (partner_id,)
    ).fetchone():
        return JSONResponse({"status": "error", "message": "Partner not found"}, 404)
    patch = req.model_dump(exclude_none=True)
    if not patch:
        return {"status": "ok"}
    sets = [f"{k} = ?" for k in patch.keys()]
    sets.append("updated_at = ?")
    params = list(patch.values()) + [_now(), partner_id]
    conn.execute(
        f"UPDATE webclaw_partner SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    conn.commit()
    _audit(conn, request, "partner", "update_partner", partner_id)
    return {"status": "ok"}


@router.post("/partners/{partner_id}/tenants/{tenant_id}")
@require_any_permission("platform.billing.manage", "platform.admin")
async def link_partner_tenant(partner_id: str, tenant_id: str, request: Request):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    if not conn.execute(
        "SELECT 1 FROM webclaw_partner WHERE id = ?", (partner_id,)
    ).fetchone():
        return JSONResponse({"status": "error", "message": "Partner not found"}, 404)
    conn.execute(
        """INSERT INTO webclaw_partner_tenant (partner_id, tenant_id, created_at)
           VALUES (?, ?, ?)
           ON CONFLICT(partner_id, tenant_id) DO NOTHING""",
        (partner_id, tenant_id, _now()),
    )
    conn.commit()
    _audit(conn, request, "partner", "link_tenant", f"{partner_id}:{tenant_id}", tenant_id)
    return {"status": "ok"}


@router.delete("/partners/{partner_id}/tenants/{tenant_id}")
@require_any_permission("platform.billing.manage", "platform.admin")
async def unlink_partner_tenant(partner_id: str, tenant_id: str, request: Request):
    conn = db.get_connection()
    ensure_growth_tables(conn)
    conn.execute(
        "DELETE FROM webclaw_partner_tenant WHERE partner_id = ? AND tenant_id = ?",
        (partner_id, tenant_id),
    )
    conn.commit()
    _audit(conn, request, "partner", "unlink_tenant", f"{partner_id}:{tenant_id}", tenant_id)
    return {"status": "ok"}
