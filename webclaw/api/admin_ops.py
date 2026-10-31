"""Platform administration — settings, audit logs, announcements, security/backups.

All endpoints require ``platform.admin``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import csv
import io

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator  # noqa: F401 — Field used by ticket models

import db
from auth.deps import get_optional_tenant
from db_access import DbConn, table_columns
from env_loader import (
    postgres_url_from_env,
    resolve_data_dir,
)
from rbac import require_any_permission, require_permission

router = APIRouter(prefix="/api/v1/admin", tags=["admin-ops"])

DEFAULT_SETTINGS = {
    "default_currency": "USD",
    "supported_currencies": ["USD", "EUR", "GBP", "AED", "INR", "AUD", "CAD"],
    "tax_policy": {
        "default_rate_percent": 0,
        "inclusive_pricing": False,
        "tax_label": "VAT",
        "require_tax_id": False,
    },
    "supported_languages": ["en", "es", "fr", "de", "ar"],
    "default_language": "en",
    "integrations": {
        "sendgrid": {"api_key": "", "from_email": "", "from_name": "HotelPilot"},
        "twilio": {
            "account_sid": "",
            "auth_token": "",
            "from_number": "",
            "whatsapp_from": "",
        },
        "smtp": {
            "host": "",
            "port": "587",
            "username": "",
            "password": "",
            "from_email": "",
            "from_name": "HotelPilot",
            "use_tls": True,
        },
        "stripe": {"publishable_key": "", "secret_key": "", "webhook_secret": ""},
    },
}

DEFAULT_SECURITY = {
    "enforce_2fa": False,
    "session_timeout_minutes": 15,
    "password_min_length": 8,
    "lockout_after_failures": 5,
    "maintenance_mode": False,
    "maintenance_message": (
        "HotelPilot is undergoing scheduled maintenance. Please try again shortly."
    ),
    "auto_backup_enabled": False,
    "auto_backup_interval_hours": 24,
    "last_auto_backup_at": None,
    "offsite_backup_enabled": False,
    "offsite_backup_url": "",
    "offsite_backup_token": "",
    "ip_allowlist_enabled": False,
    "ip_allowlist": [],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


OPS_DDL = """
CREATE TABLE IF NOT EXISTS webclaw_platform_settings (
    id          TEXT PRIMARY KEY CHECK (id = 'default'),
    data        TEXT NOT NULL,
    updated_at  TEXT,
    updated_by  TEXT
);

CREATE TABLE IF NOT EXISTS webclaw_platform_audit (
    id              TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    user_id         TEXT,
    user_email      TEXT,
    tenant_id       TEXT,
    action_category TEXT NOT NULL,
    action          TEXT NOT NULL,
    ip_address      TEXT,
    status          TEXT NOT NULL DEFAULT 'success'
                    CHECK(status IN ('success','failure')),
    detail          TEXT,
    before_json     TEXT,
    after_json      TEXT
);
CREATE INDEX IF NOT EXISTS idx_platform_audit_ts
    ON webclaw_platform_audit(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_platform_audit_cat
    ON webclaw_platform_audit(action_category);

CREATE TABLE IF NOT EXISTS webclaw_support_macro (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    category        TEXT NOT NULL DEFAULT 'general',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    updated_by      TEXT
);

CREATE TABLE IF NOT EXISTS webclaw_announcement (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    channel         TEXT NOT NULL DEFAULT 'banner'
                    CHECK(channel IN ('banner','email','both')),
    audience        TEXT NOT NULL DEFAULT 'all',
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK(status IN ('draft','published','archived')),
    published_at    TEXT,
    created_by      TEXT,
    created_by_email TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webclaw_security_policy (
    id                      TEXT PRIMARY KEY CHECK (id = 'default'),
    enforce_2fa             INTEGER NOT NULL DEFAULT 0,
    session_timeout_minutes INTEGER NOT NULL DEFAULT 15,
    password_min_length     INTEGER NOT NULL DEFAULT 8,
    lockout_after_failures  INTEGER NOT NULL DEFAULT 5,
    updated_at              TEXT,
    updated_by              TEXT
);

CREATE TABLE IF NOT EXISTS webclaw_backup_log (
    id              TEXT PRIMARY KEY,
    filename        TEXT NOT NULL,
    path            TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'completed'
                    CHECK(status IN ('completed','failed')),
    created_by      TEXT,
    created_by_email TEXT,
    created_at      TEXT NOT NULL,
    downloaded_at   TEXT,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS webclaw_support_ticket (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    hotel_name      TEXT,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open','in_progress','waiting','resolved','closed')),
    priority        TEXT NOT NULL DEFAULT 'normal'
                    CHECK(priority IN ('low','normal','high','urgent')),
    requester_email TEXT,
    assignee_email  TEXT,
    created_by      TEXT,
    created_by_email TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    resolved_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_support_ticket_status
    ON webclaw_support_ticket(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_support_ticket_tenant
    ON webclaw_support_ticket(tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS webclaw_support_ticket_note (
    id              TEXT PRIMARY KEY,
    ticket_id       TEXT NOT NULL REFERENCES webclaw_support_ticket(id),
    body            TEXT NOT NULL,
    author_email    TEXT,
    author_id       TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_support_note_ticket
    ON webclaw_support_ticket_note(ticket_id, created_at);

CREATE TABLE IF NOT EXISTS webclaw_ops_alert (
    id              TEXT PRIMARY KEY,
    severity        TEXT NOT NULL DEFAULT 'info'
                    CHECK(severity IN ('info','warning','critical')),
    category        TEXT NOT NULL DEFAULT 'ops',
    title           TEXT NOT NULL,
    detail          TEXT NOT NULL DEFAULT '',
    payload_json    TEXT NOT NULL DEFAULT '{}',
    is_acked        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    acked_at        TEXT,
    acked_by        TEXT
);
CREATE INDEX IF NOT EXISTS idx_ops_alert_open
    ON webclaw_ops_alert(is_acked, created_at DESC);

CREATE TABLE IF NOT EXISTS webclaw_ops_job (
    job_key         TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    is_paused       INTEGER NOT NULL DEFAULT 0,
    last_run_at     TEXT,
    last_status     TEXT,
    last_detail     TEXT,
    updated_at      TEXT,
    updated_by      TEXT
);
"""

DEFAULT_OPS_JOBS = (
    {
        "job_key": "night_audit",
        "label": "Night audit",
        "description": "Polls hotels and runs due night audits",
    },
    {
        "job_key": "yield_pricing",
        "label": "Yield pricing",
        "description": "Applies scheduled yield / dynamic pricing rules",
    },
    {
        "job_key": "platform_ops",
        "label": "Platform ops",
        "description": "Announcements, auto-backups, and cancelled-tenant purge",
    },
)


def _safe_ddl(conn: DbConn, ddl: str) -> bool:
    """Run a single DDL statement; rollback on failure so Postgres stays usable."""
    try:
        conn.execute(ddl)
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def ensure_ops_tables(conn: DbConn) -> None:
    from db_access import run_ddl

    run_ddl(conn, OPS_DDL)
    # Additive columns for P1/P4 (scheduled announcements + region targeting).
    ann_cols = set(table_columns(conn, "webclaw_announcement") or [])
    for col, ddl in (
        ("scheduled_at", "ALTER TABLE webclaw_announcement ADD COLUMN scheduled_at TEXT"),
        ("expires_at", "ALTER TABLE webclaw_announcement ADD COLUMN expires_at TEXT"),
        ("regions_json", "ALTER TABLE webclaw_announcement ADD COLUMN regions_json TEXT"),
        ("email_delivered_at", "ALTER TABLE webclaw_announcement ADD COLUMN email_delivered_at TEXT"),
    ):
        if col not in ann_cols:
            if _safe_ddl(conn, ddl):
                ann_cols.add(col)
    sec_cols = set(table_columns(conn, "webclaw_security_policy") or [])
    for col, ddl in (
        (
            "maintenance_mode",
            "ALTER TABLE webclaw_security_policy ADD COLUMN maintenance_mode INTEGER NOT NULL DEFAULT 0",
        ),
        (
            "maintenance_message",
            "ALTER TABLE webclaw_security_policy ADD COLUMN maintenance_message TEXT",
        ),
        (
            "auto_backup_enabled",
            "ALTER TABLE webclaw_security_policy ADD COLUMN auto_backup_enabled INTEGER NOT NULL DEFAULT 0",
        ),
        (
            "auto_backup_interval_hours",
            "ALTER TABLE webclaw_security_policy ADD COLUMN auto_backup_interval_hours INTEGER NOT NULL DEFAULT 24",
        ),
        (
            "last_auto_backup_at",
            "ALTER TABLE webclaw_security_policy ADD COLUMN last_auto_backup_at TEXT",
        ),
        (
            "offsite_backup_enabled",
            "ALTER TABLE webclaw_security_policy ADD COLUMN offsite_backup_enabled INTEGER NOT NULL DEFAULT 0",
        ),
        (
            "offsite_backup_url",
            "ALTER TABLE webclaw_security_policy ADD COLUMN offsite_backup_url TEXT",
        ),
        (
            "offsite_backup_token",
            "ALTER TABLE webclaw_security_policy ADD COLUMN offsite_backup_token TEXT",
        ),
        (
            "ip_allowlist_enabled",
            "ALTER TABLE webclaw_security_policy ADD COLUMN ip_allowlist_enabled INTEGER NOT NULL DEFAULT 0",
        ),
        (
            "ip_allowlist_json",
            "ALTER TABLE webclaw_security_policy ADD COLUMN ip_allowlist_json TEXT",
        ),
    ):
        if col not in sec_cols:
            if _safe_ddl(conn, ddl):
                sec_cols.add(col)

    audit_cols = set(table_columns(conn, "webclaw_platform_audit") or [])
    for col, ddl in (
        ("before_json", "ALTER TABLE webclaw_platform_audit ADD COLUMN before_json TEXT"),
        ("after_json", "ALTER TABLE webclaw_platform_audit ADD COLUMN after_json TEXT"),
    ):
        if col not in audit_cols:
            if _safe_ddl(conn, ddl):
                audit_cols.add(col)

    ticket_cols = set(table_columns(conn, "webclaw_support_ticket") or [])
    for col, ddl in (
        ("sla_due_at", "ALTER TABLE webclaw_support_ticket ADD COLUMN sla_due_at TEXT"),
        (
            "first_response_at",
            "ALTER TABLE webclaw_support_ticket ADD COLUMN first_response_at TEXT",
        ),
        (
            "sla_breached",
            "ALTER TABLE webclaw_support_ticket ADD COLUMN sla_breached INTEGER NOT NULL DEFAULT 0",
        ),
    ):
        if col not in ticket_cols:
            if _safe_ddl(conn, ddl):
                ticket_cols.add(col)

    now = _now()
    for spec in DEFAULT_OPS_JOBS:
        try:
            exists = conn.execute(
                "SELECT 1 FROM webclaw_ops_job WHERE job_key = ?", (spec["job_key"],)
            ).fetchone()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            exists = None
        if not exists:
            try:
                conn.execute(
                    """INSERT INTO webclaw_ops_job
                       (job_key, label, description, is_paused, updated_at)
                       VALUES (?, ?, ?, 0, ?)""",
                    (spec["job_key"], spec["label"], spec["description"], now),
                )
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
    # Seed a couple of support macros when empty
    try:
        macro_count = int(
            conn.execute("SELECT COUNT(*) FROM webclaw_support_macro").fetchone()[0] or 0
        )
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        macro_count = -1
    if macro_count == 0:
        for name, body, cat in (
            (
                "Welcome / ack",
                "Thanks for reaching out — we’ve received your request and will update you shortly.",
                "ack",
            ),
            (
                "Need more info",
                "Could you share the hotel name, approximate time of the issue, and any error message you saw?",
                "triage",
            ),
            (
                "Resolved follow-up",
                "We’ve applied a fix on our side. Please confirm if everything looks good on your end.",
                "resolve",
            ),
        ):
            try:
                conn.execute(
                    """INSERT INTO webclaw_support_macro
                       (id, name, body, category, is_active, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 1, ?, ?)""",
                    (str(uuid.uuid4()), name, body, cat, now, now),
                )
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
    try:
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def notify_ops(
    category: str,
    payload: dict | None = None,
    *,
    severity: str = "warning",
    title: str | None = None,
) -> str:
    """Record an ops alert for the platform health console. Returns alert id."""
    conn = db.get_connection()
    ensure_ops_tables(conn)
    aid = str(uuid.uuid4())
    body = payload or {}
    alert_title = title or str(category).replace("_", " ").title()
    detail = ""
    if isinstance(body, dict):
        detail = json.dumps(body)[:2000]
    sev = severity if severity in ("info", "warning", "critical") else "warning"
    conn.execute(
        """INSERT INTO webclaw_ops_alert
           (id, severity, category, title, detail, payload_json, is_acked, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
        (aid, sev, category, alert_title, detail, json.dumps(body), _now()),
    )
    conn.commit()
    return aid


def is_job_paused(job_key: str) -> bool:
    conn = db.get_connection()
    ensure_ops_tables(conn)
    row = conn.execute(
        "SELECT is_paused FROM webclaw_ops_job WHERE job_key = ?", (job_key,)
    ).fetchone()
    if not row:
        return False
    return bool(row[0] if isinstance(row, tuple) else row["is_paused"])


def mark_job_run(job_key: str, status: str = "ok", detail: str = "") -> None:
    conn = db.get_connection()
    ensure_ops_tables(conn)
    conn.execute(
        """UPDATE webclaw_ops_job
           SET last_run_at = ?, last_status = ?, last_detail = ?, updated_at = ?
           WHERE job_key = ?""",
        (_now(), status, (detail or "")[:500], _now(), job_key),
    )
    conn.commit()


def _maybe_offsite_copy(local_path: Path, policy: dict | None = None) -> dict | None:
    """POST the dump file to a configured offsite webhook/URL when enabled."""
    pol = policy or {}
    if not pol.get("offsite_backup_enabled"):
        return None
    url = (pol.get("offsite_backup_url") or "").strip()
    if not url:
        return {"status": "skipped", "reason": "no_url"}
    token = (pol.get("offsite_backup_token") or "").strip()
    try:
        import urllib.request

        data = local_path.read_bytes()
        req = urllib.request.Request(
            url,
            data=data,
            method="PUT",
            headers={
                "Content-Type": "application/sql",
                "Content-Length": str(len(data)),
                "X-Backup-Filename": local_path.name,
                **({"Authorization": f"Bearer {token}"} if token else {}),
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            code = getattr(resp, "status", None) or 200
        return {"status": "ok", "http_status": int(code), "url": url}
    except Exception as e:
        try:
            notify_ops(
                "offsite_backup_failed",
                {"filename": local_path.name, "error": str(e)[:300]},
                severity="critical",
                title="Offsite backup failed",
            )
        except Exception:
            pass
        return {"status": "error", "error": str(e)[:300]}


def get_maintenance_state(conn: DbConn | None = None) -> dict:
    """Public maintenance flag for login / status endpoints."""
    c = conn or db.get_connection()
    policy = _load_security(c)
    return {
        "maintenance_mode": bool(policy.get("maintenance_mode")),
        "maintenance_message": policy.get("maintenance_message")
        or DEFAULT_SECURITY["maintenance_message"],
    }


def activate_due_announcements(conn: DbConn | None = None) -> int:
    """Publish announcements whose scheduled_at has arrived; archive expired ones."""
    c = conn or db.get_connection()
    ensure_ops_tables(c)
    now = _now()
    published = 0
    newly_published: list[str] = []
    try:
        due = c.execute(
            """SELECT id FROM webclaw_announcement
               WHERE status = 'draft'
                 AND scheduled_at IS NOT NULL
                 AND scheduled_at <= ?""",
            (now,),
        ).fetchall()
        newly_published = [
            str(r[0] if isinstance(r, tuple) else r["id"]) for r in due
        ]
        cur = c.execute(
            """UPDATE webclaw_announcement
               SET status = 'published',
                   published_at = COALESCE(published_at, ?),
                   updated_at = ?
               WHERE status = 'draft'
                 AND scheduled_at IS NOT NULL
                 AND scheduled_at <= ?""",
            (now, now, now),
        )
        published = cur.rowcount if cur is not None else 0
        c.execute(
            """UPDATE webclaw_announcement
               SET status = 'archived', updated_at = ?
               WHERE status = 'published'
                 AND expires_at IS NOT NULL
                 AND expires_at <= ?""",
            (now, now),
        )
        c.commit()
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
        return 0
    for ann_id in newly_published:
        try:
            deliver_announcement_emails_sync(ann_id, c)
        except Exception:
            pass
    return int(published or 0)


def _parse_regions(raw) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(x).strip().upper() for x in raw if str(x).strip()]
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, list):
            return [str(x).strip().upper() for x in data if str(x).strip()]
    except Exception:
        pass
    return []


def announcement_matches_tenant(
    *,
    audience: str,
    regions: list[str],
    plan_code: str | None,
    country: str | None,
) -> bool:
    """Tier + optional country/region targeting."""
    aud = (audience or "all").strip().lower()
    plan = (plan_code or "starter").strip().lower()
    if plan == "professional":
        plan = "business"
    if aud not in {"all", plan}:
        return False
    if not regions:
        return True
    cc = (country or "").strip().upper()
    if not cc:
        return False
    return cc in regions or any(cc.startswith(r) or r.startswith(cc) for r in regions)


def _tenant_targets_for_announcement(audience: str, regions: list[str]) -> list[dict]:
    """Resolve hotels matching plan audience + region list."""
    from admin_platform import _erp as erp_fn, TIER_TO_PLAN

    erp = erp_fn()
    if not erp:
        return []
    out: list[dict] = []
    try:
        rows = erp.execute(
            """SELECT id, hotel_name, admin_email, country, subscription_tier, status
               FROM pms_tenant WHERE status IN ('active', 'trial', 'onboarding')"""
        ).fetchall()
        for r in rows:
            tid = str(r[0] if isinstance(r, tuple) else r["id"])
            hotel = r[1] if isinstance(r, tuple) else r["hotel_name"]
            email = r[2] if isinstance(r, tuple) else r["admin_email"]
            country = r[3] if isinstance(r, tuple) else r["country"]
            tier = r[4] if isinstance(r, tuple) else r["subscription_tier"]
            plan = TIER_TO_PLAN.get(str(tier or "").lower(), "starter")
            if announcement_matches_tenant(
                audience=audience,
                regions=regions,
                plan_code=plan,
                country=str(country or ""),
            ):
                out.append(
                    {
                        "tenant_id": tid,
                        "hotel_name": hotel,
                        "admin_email": email,
                        "country": country,
                        "plan_code": plan,
                    }
                )
    except Exception:
        pass
    finally:
        erp.close()
    return out


async def deliver_announcement_emails(
    announcement_id: str, conn: DbConn | None = None
) -> dict:
    """Send email channel announcements to matching hotel admins (once)."""
    c = conn or db.get_connection()
    ensure_ops_tables(c)
    row = c.execute(
        "SELECT * FROM webclaw_announcement WHERE id = ?", (announcement_id,)
    ).fetchone()
    if not row:
        return {"ok": False, "error": "not_found"}
    d = _announcement_dict(row)
    if d.get("status") != "published" or d.get("channel") not in ("email", "both"):
        return {"ok": True, "skipped": True, "reason": "not_email_channel"}
    if d.get("email_delivered_at"):
        return {"ok": True, "skipped": True, "reason": "already_delivered"}

    targets = _tenant_targets_for_announcement(
        str(d.get("audience") or "all"),
        list(d.get("regions") or []),
    )
    sent = 0
    errors = 0
    from notifications.channels import send_email

    for t in targets:
        email = (t.get("admin_email") or "").strip()
        if not email or "@" not in email:
            continue
        try:
            result = await send_email(
                to=email,
                subject=str(d.get("title") or "Announcement"),
                body=str(d.get("body") or ""),
                tenant_id=t.get("tenant_id"),
            )
            if result.get("ok"):
                sent += 1
            else:
                errors += 1
        except Exception:
            errors += 1

    now = _now()
    c.execute(
        "UPDATE webclaw_announcement SET email_delivered_at = ?, updated_at = ? WHERE id = ?",
        (now, now, announcement_id),
    )
    c.commit()
    return {"ok": True, "recipients": len(targets), "sent": sent, "errors": errors}


def deliver_announcement_emails_sync(announcement_id: str, conn: DbConn | None = None) -> dict:
    """Sync wrapper for scheduler ticks."""
    import asyncio

    try:
        return asyncio.run(deliver_announcement_emails(announcement_id, conn))
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def run_scheduled_backup_if_due() -> dict | None:
    """Create an automated snapshot when enabled and the interval has elapsed."""
    conn = db.get_connection()
    ensure_ops_tables(conn)
    policy = _load_security(conn)
    if not policy.get("auto_backup_enabled"):
        return None
    interval = max(1, int(policy.get("auto_backup_interval_hours") or 24))
    last = policy.get("last_auto_backup_at")
    now = datetime.now(timezone.utc)
    if last:
        try:
            last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            hours = (now - last_dt).total_seconds() / 3600.0
            if hours < interval:
                return None
        except Exception:
            pass
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    filename = f"webclaw-auto-{stamp}.sql"
    dest = _backups_dir() / filename
    backup_id = str(uuid.uuid4())
    try:
        _pg_dump_platform(dest)
        size = dest.stat().st_size
        _maybe_offsite_copy(dest, policy)
        conn.execute(
            """INSERT INTO webclaw_backup_log
               (id, filename, path, size_bytes, status, created_by, created_by_email, created_at)
               VALUES (?, ?, ?, ?, 'completed', NULL, 'scheduler', ?)""",
            (backup_id, filename, str(dest), size, _now()),
        )
        conn.execute(
            """UPDATE webclaw_security_policy
               SET last_auto_backup_at = ?, updated_at = ?
               WHERE id = 'default'""",
            (_now(), _now()),
        )
        # Ensure a policy row exists so last_auto_backup_at is stored.
        if conn.execute(
            "SELECT 1 FROM webclaw_security_policy WHERE id = 'default'"
        ).fetchone() is None:
            conn.execute(
                """INSERT INTO webclaw_security_policy
                   (id, enforce_2fa, session_timeout_minutes, password_min_length,
                    lockout_after_failures, maintenance_mode, maintenance_message,
                    auto_backup_enabled, auto_backup_interval_hours, last_auto_backup_at,
                    updated_at, updated_by)
                   VALUES ('default', 0, 15, 8, 5, 0, ?, 1, ?, ?, ?, 'scheduler')""",
                (
                    DEFAULT_SECURITY["maintenance_message"],
                    interval,
                    _now(),
                    _now(),
                ),
            )
        conn.commit()
        write_platform_audit(
            conn,
            user_id=None,
            user_email="scheduler",
            tenant_id=None,
            action_category="backup",
            action="auto_snapshot",
            ip_address=None,
            detail=filename,
        )
        return {"id": backup_id, "filename": filename, "size_bytes": size}
    except Exception as e:
        try:
            conn.execute(
                """INSERT INTO webclaw_backup_log
                   (id, filename, path, size_bytes, status, created_by, created_by_email, created_at, error)
                   VALUES (?, ?, ?, 0, 'failed', NULL, 'scheduler', ?, ?)""",
                (backup_id, filename, str(dest), _now(), str(e)),
            )
            conn.commit()
        except Exception:
            pass
        return None


def _ticket_dict(row, notes: list | None = None) -> dict:
    def g(key: str, idx: int, default=None):
        if hasattr(row, "keys"):
            keys = row.keys()
            if key in keys:
                return row[key]
            return default
        if isinstance(row, tuple) and len(row) > idx:
            return row[idx]
        return default

    return {
        "id": g("id", 0),
        "tenant_id": g("tenant_id", 1),
        "hotel_name": g("hotel_name", 2),
        "subject": g("subject", 3),
        "body": g("body", 4),
        "status": g("status", 5),
        "priority": g("priority", 6),
        "requester_email": g("requester_email", 7),
        "assignee_email": g("assignee_email", 8),
        "created_by": g("created_by", 9),
        "created_by_email": g("created_by_email", 10),
        "created_at": g("created_at", 11),
        "updated_at": g("updated_at", 12),
        "resolved_at": g("resolved_at", 13),
        "sla_due_at": g("sla_due_at", 14),
        "first_response_at": g("first_response_at", 15),
        "sla_breached": bool(g("sla_breached", 16, 0) or 0),
        "notes": notes or [],
    }


def list_tickets_for_tenant(conn: DbConn, tenant_id: str, limit: int = 10) -> list[dict]:
    ensure_ops_tables(conn)
    rows = conn.execute(
        """SELECT id, tenant_id, hotel_name, subject, body, status, priority,
                  requester_email, assignee_email, created_by, created_by_email,
                  created_at, updated_at, resolved_at,
                  sla_due_at, first_response_at, sla_breached
           FROM webclaw_support_ticket
           WHERE tenant_id = ?
           ORDER BY datetime(updated_at) DESC
           LIMIT ?""",
        (tenant_id, limit),
    ).fetchall()
    return [_ticket_dict(r) for r in rows]


def write_platform_audit(
    conn: DbConn,
    *,
    user_id: str | None,
    user_email: str | None,
    tenant_id: str | None,
    action_category: str,
    action: str,
    ip_address: str | None,
    status: str = "success",
    detail: str = "",
    before: dict | list | str | None = None,
    after: dict | list | str | None = None,
) -> None:
    """Append-only platform audit row (never updated or deleted by app code)."""
    ensure_ops_tables(conn)

    def _ser(val) -> str | None:
        if val is None:
            return None
        if isinstance(val, str):
            return val
        try:
            return json.dumps(val, default=str)
        except Exception:
            return str(val)

    before_s = _ser(before)
    after_s = _ser(after)
    cols = set(table_columns(conn, "webclaw_platform_audit") or [])
    if "before_json" in cols and "after_json" in cols:
        conn.execute(
            """INSERT INTO webclaw_platform_audit
               (id, timestamp, user_id, user_email, tenant_id, action_category,
                action, ip_address, status, detail, before_json, after_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                _now(),
                user_id,
                user_email,
                tenant_id,
                action_category,
                action,
                ip_address,
                status if status in ("success", "failure") else "success",
                detail,
                before_s,
                after_s,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO webclaw_platform_audit
               (id, timestamp, user_id, user_email, tenant_id, action_category,
                action, ip_address, status, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                _now(),
                user_id,
                user_email,
                tenant_id,
                action_category,
                action,
                ip_address,
                status if status in ("success", "failure") else "success",
                detail,
            ),
        )
    conn.commit()


SLA_HOURS = {
    "urgent": 4,
    "high": 8,
    "normal": 24,
    "low": 72,
}


def _sla_due_at(priority: str, created_at: str | None = None) -> str:
    from datetime import timedelta

    hours = SLA_HOURS.get((priority or "normal").lower(), 24)
    base = datetime.now(timezone.utc)
    if created_at:
        try:
            base = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return (base + timedelta(hours=hours)).isoformat()


def check_sla_breaches(conn: DbConn | None = None) -> int:
    """Mark open tickets past sla_due_at and emit ops alerts. Returns newly breached count."""
    c = conn or db.get_connection()
    ensure_ops_tables(c)
    now = _now()
    rows = c.execute(
        """SELECT id, tenant_id, hotel_name, subject, priority, sla_due_at
           FROM webclaw_support_ticket
           WHERE status IN ('open', 'in_progress', 'waiting')
             AND sla_due_at IS NOT NULL
             AND sla_due_at <= ?
             AND COALESCE(sla_breached, 0) = 0""",
        (now,),
    ).fetchall()
    n = 0
    alerts: list[dict] = []
    for r in rows:
        tid = r[0] if isinstance(r, tuple) else r["id"]
        c.execute(
            "UPDATE webclaw_support_ticket SET sla_breached = 1, updated_at = ? WHERE id = ?",
            (now, tid),
        )
        n += 1
        alerts.append(
            {
                "ticket_id": tid,
                "tenant_id": r[1] if isinstance(r, tuple) else r["tenant_id"],
                "subject": r[3] if isinstance(r, tuple) else r["subject"],
                "priority": r[4] if isinstance(r, tuple) else r["priority"],
                "sla_due_at": r[5] if isinstance(r, tuple) else r["sla_due_at"],
            }
        )
    if n:
        try:
            c.commit()
        except Exception:
            try:
                c.rollback()
            except Exception:
                pass
            # Retry mark on a fresh connection if the shared one dropped
            c2 = db.get_connection()
            ensure_ops_tables(c2)
            for a in alerts:
                c2.execute(
                    """UPDATE webclaw_support_ticket
                       SET sla_breached = 1, updated_at = ?
                       WHERE id = ? AND COALESCE(sla_breached, 0) = 0""",
                    (now, a["ticket_id"]),
                )
            c2.commit()
    for a in alerts:
        try:
            notify_ops(
                "sla_breach",
                a,
                severity="critical",
                title="Support SLA breached",
            )
        except Exception:
            pass
    return n


def ip_in_allowlist(client_ip: str | None, allowlist: list[str]) -> bool:
    """True if client_ip matches an exact IP or simple CIDR (/8,/16,/24) entry."""
    if not allowlist:
        return True
    if not client_ip:
        return False
    ip = client_ip.strip()
    for entry in allowlist:
        rule = (entry or "").strip()
        if not rule:
            continue
        if "/" not in rule:
            if ip == rule:
                return True
            continue
        try:
            network, bits_s = rule.split("/", 1)
            bits = int(bits_s)
            ip_parts = [int(x) for x in ip.split(".")]
            net_parts = [int(x) for x in network.split(".")]
            if len(ip_parts) != 4 or len(net_parts) != 4:
                continue
            mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF if 0 <= bits <= 32 else 0
            ip_int = (ip_parts[0] << 24) | (ip_parts[1] << 16) | (ip_parts[2] << 8) | ip_parts[3]
            net_int = (net_parts[0] << 24) | (net_parts[1] << 16) | (net_parts[2] << 8) | net_parts[3]
            if (ip_int & mask) == (net_int & mask):
                return True
        except Exception:
            continue
    return False


def check_ip_allowlist(client_ip: str | None, conn: DbConn | None = None) -> dict:
    """Return {allowed: bool, enabled: bool}. When disabled, always allowed."""
    c = conn or db.get_connection()
    policy = _load_security(c)
    enabled = bool(policy.get("ip_allowlist_enabled"))
    allowlist = policy.get("ip_allowlist") or []
    if not enabled:
        return {"allowed": True, "enabled": False}
    return {
        "allowed": ip_in_allowlist(client_ip, list(allowlist)),
        "enabled": True,
    }


def _mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "•" * len(value)
    return "•" * (len(value) - keep) + value[-keep:]


def _load_settings(conn: DbConn) -> dict:
    ensure_ops_tables(conn)
    row = conn.execute(
        "SELECT data FROM webclaw_platform_settings WHERE id = 'default'"
    ).fetchone()
    if not row:
        return json.loads(json.dumps(DEFAULT_SETTINGS))
    raw = row[0] if isinstance(row, tuple) else row["data"]
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return json.loads(json.dumps(DEFAULT_SETTINGS))
    # Deep-merge defaults so new keys appear after upgrades
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "integrations" and isinstance(v, dict):
                for ik, iv in v.items():
                    merged["integrations"].setdefault(ik, {})
                    if isinstance(iv, dict):
                        merged["integrations"][ik].update(iv)
            elif k == "tax_policy" and isinstance(v, dict):
                merged["tax_policy"].update(v)
            else:
                merged[k] = v
    return merged


def _public_settings(data: dict, *, reveal_secrets: bool = False) -> dict:
    out = json.loads(json.dumps(data))
    if reveal_secrets:
        return out
    integ = out.get("integrations") or {}
    for provider, fields in integ.items():
        if not isinstance(fields, dict):
            continue
        for key, val in list(fields.items()):
            if any(s in key for s in ("key", "token", "secret", "sid")) and val:
                fields[key] = _mask_secret(str(val))
    return out


def _backups_dir() -> Path:
    base = Path(os.environ.get("WEBCLAW_BACKUP_DIR") or "")
    if not base.parts:
        base = Path(resolve_data_dir()) / "backups"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _platform_postgres_url() -> str:
    for candidate in (
        os.environ.get("WEBCLAW_DB_URL"),
        os.environ.get("ERPCLAW_DB_URL"),
        postgres_url_from_env(),
    ):
        if candidate and str(candidate).strip().lower().startswith(
            ("postgres://", "postgresql://")
        ):
            return str(candidate).strip()
    raise RuntimeError(
        "PostgreSQL URL required for platform backups "
        "(WEBCLAW_DB_URL or DB_HOST/DB_*/DB_PASSWORD)"
    )


def _pg_dump_platform(dest: Path) -> None:
    """Dump the ``webclaw`` schema with ``pg_dump`` (plain SQL)."""
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        raise RuntimeError(
            "pg_dump not found on PATH. Install PostgreSQL client tools "
            "or use a managed backup for this deployment."
        )
    url = _platform_postgres_url()
    parsed = urlparse(url)
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    # Prefer discrete libpq vars so special characters in the URL are safe.
    cmd = [
        pg_dump,
        "--schema=webclaw",
        "--no-owner",
        "--no-acl",
        "--format=plain",
        f"--file={dest}",
    ]
    if parsed.hostname:
        cmd.append(f"--host={parsed.hostname}")
    if parsed.port:
        cmd.append(f"--port={parsed.port}")
    if parsed.username:
        cmd.append(f"--username={unquote(parsed.username)}")
    dbname = unquote((parsed.path or "/pms").lstrip("/")) or "pms"
    cmd.append(dbname)
    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "pg_dump failed").strip()
        raise RuntimeError(err)


# ── Settings ─────────────────────────────────────────────────────────────────

class PlatformSettingsUpdate(BaseModel):
    default_currency: str | None = None
    supported_currencies: list[str] | None = None
    tax_policy: dict | None = None
    supported_languages: list[str] | None = None
    default_language: str | None = None
    integrations: dict | None = None


@router.get("/platform-settings")
@require_any_permission("platform.settings.manage", "platform.admin")
async def get_platform_settings(request: Request):
    conn = db.get_connection()
    data = _load_settings(conn)
    return {"status": "ok", "settings": _public_settings(data)}


@router.put("/platform-settings")
@require_any_permission("platform.settings.manage", "platform.admin")
async def update_platform_settings(request: Request):
    try:
        body = await request.json()
        req = PlatformSettingsUpdate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    conn = db.get_connection()
    current = _load_settings(conn)
    patch = req.model_dump(exclude_none=True)

    # Merge integrations without wiping secrets when masked placeholders sent
    if "integrations" in patch and isinstance(patch["integrations"], dict):
        for provider, fields in patch["integrations"].items():
            current["integrations"].setdefault(provider, {})
            if not isinstance(fields, dict):
                continue
            for k, v in fields.items():
                if isinstance(v, str) and "•" in v:
                    continue  # keep existing secret
                current["integrations"][provider][k] = v
        del patch["integrations"]

    if "tax_policy" in patch and isinstance(patch["tax_policy"], dict):
        current["tax_policy"].update(patch.pop("tax_policy"))

    current.update(patch)

    user_id, email = _actor(request)
    conn.execute(
        """INSERT INTO webclaw_platform_settings (id, data, updated_at, updated_by)
           VALUES ('default', ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             data = excluded.data,
             updated_at = excluded.updated_at,
             updated_by = excluded.updated_by""",
        (json.dumps(current), _now(), email or user_id),
    )
    conn.commit()
    write_platform_audit(
        conn,
        user_id=user_id,
        user_email=email,
        tenant_id=None,
        action_category="settings",
        action="update_platform_settings",
        ip_address=_client_ip(request),
        detail="Platform settings updated",
    )
    return {"status": "ok", "settings": _public_settings(current)}


# ── Integrations control plane ───────────────────────────────────────────────

def _integration_health_snapshot() -> dict:
    """Configured + lightweight connectivity status for platform providers."""
    from notifications.channels import (
        email_configured,
        load_messaging_credentials,
        sms_configured,
        whatsapp_configured,
    )

    creds = load_messaging_credentials()
    sg = creds.get("sendgrid") or {}
    smtp = creds.get("smtp") or {}
    tw = creds.get("twilio") or {}

    stripe_ok = False
    stripe_detail = "unconfigured"
    try:
        from stripe_billing import platform_stripe_configured, platform_stripe_keys

        stripe_ok = platform_stripe_configured()
        keys = platform_stripe_keys()
        if stripe_ok:
            stripe_detail = "secret key present"
            # Lightweight Stripe reachability (Account.retrieve)
            try:
                stripe = __import__("stripe_billing", fromlist=["get_stripe_client"]).get_stripe_client(
                    keys.get("secret_key")
                )
                stripe.Account.retrieve()
                stripe_detail = "reachable"
            except Exception as e:
                stripe_detail = f"configured but unreachable: {str(e)[:120]}"
                stripe_ok = False
    except Exception as e:
        stripe_detail = str(e)[:120]

    email_ok = email_configured(creds)
    email_provider = (
        "sendgrid"
        if sg.get("api_key") and sg.get("from_email")
        else ("smtp" if smtp.get("host") else None)
    )
    return {
        "sendgrid": {
            "configured": bool(sg.get("api_key") and sg.get("from_email")),
            "status": "ok" if (sg.get("api_key") and sg.get("from_email")) else "unconfigured",
            "from_email": sg.get("from_email") or "",
        },
        "smtp": {
            "configured": bool(smtp.get("host")),
            "status": "ok" if smtp.get("host") else "unconfigured",
            "host": smtp.get("host") or "",
        },
        "email": {
            "configured": email_ok,
            "status": "ok" if email_ok else "unconfigured",
            "provider": email_provider,
        },
        "twilio_sms": {
            "configured": sms_configured(creds),
            "status": "ok" if sms_configured(creds) else "unconfigured",
        },
        "twilio_whatsapp": {
            "configured": whatsapp_configured(creds),
            "status": "ok" if whatsapp_configured(creds) else "unconfigured",
        },
        "stripe": {
            "configured": bool(stripe_ok) or stripe_detail.startswith("configured"),
            "status": "ok" if stripe_ok and stripe_detail == "reachable" else (
                "degraded" if "unreachable" in stripe_detail else "unconfigured"
            ),
            "detail": stripe_detail,
        },
    }


@router.get("/integrations/health")
@require_any_permission("platform.settings.manage", "platform.admin")
async def integrations_health(request: Request):
    health = _integration_health_snapshot()
    # Recent platform-wide delivery samples
    deliveries = []
    try:
        from notifications.store import ensure_notification_tables

        conn = ensure_notification_tables()
        rows = conn.execute(
            """SELECT id, tenant_id, trigger_key, channel, recipient, status, detail, created_at
               FROM webclaw_notification_log
               ORDER BY created_at DESC LIMIT 25"""
        ).fetchall()
        for r in rows:
            deliveries.append(
                {
                    "id": r[0] if isinstance(r, tuple) else r["id"],
                    "tenant_id": r[1] if isinstance(r, tuple) else r["tenant_id"],
                    "trigger_key": r[2] if isinstance(r, tuple) else r["trigger_key"],
                    "channel": r[3] if isinstance(r, tuple) else r["channel"],
                    "recipient": r[4] if isinstance(r, tuple) else r["recipient"],
                    "status": r[5] if isinstance(r, tuple) else r["status"],
                    "detail": r[6] if isinstance(r, tuple) else r["detail"],
                    "created_at": r[7] if isinstance(r, tuple) else r["created_at"],
                }
            )
    except Exception:
        deliveries = []
    return {"status": "ok", "providers": health, "recent_deliveries": deliveries}


@router.post("/integrations/{provider}/test")
@require_any_permission("platform.settings.manage", "platform.admin")
async def test_integration(provider: str, request: Request):
    """Connectivity / dry-run test for a messaging or billing provider."""
    provider = (provider or "").strip().lower()
    try:
        body = await request.json()
    except Exception:
        body = {}
    to = str((body or {}).get("to") or "").strip()
    user_id, email = _actor(request)

    result: dict = {"provider": provider, "ok": False}
    if provider in ("sendgrid", "smtp", "email"):
        if not to or "@" not in to:
            to = email or ""
        if not to or "@" not in to:
            return JSONResponse(
                {"status": "error", "message": "Provide to= email for the test send"},
                400,
            )
        from notifications.channels import send_email

        result = await send_email(
            to=to,
            subject="HotelPilot integration test",
            body="This is a platform integration test message from HotelPilot Central Admin.",
        )
        result["provider"] = provider
    elif provider in ("twilio", "sms"):
        if not to:
            return JSONResponse(
                {"status": "error", "message": "Provide to= phone number (E.164)"},
                400,
            )
        from notifications.channels import send_sms

        result = await send_sms(to=to, body="HotelPilot SMS integration test")
        result["provider"] = "sms"
    elif provider == "stripe":
        snap = _integration_health_snapshot().get("stripe") or {}
        result = {
            "provider": "stripe",
            "ok": snap.get("status") == "ok",
            "detail": snap.get("detail"),
            "status": snap.get("status"),
        }
    else:
        return JSONResponse(
            {"status": "error", "message": f"Unknown provider: {provider}"},
            400,
        )

    write_platform_audit(
        db.get_connection(),
        user_id=user_id,
        user_email=email,
        tenant_id=None,
        action_category="integrations",
        action=f"test_{provider}",
        ip_address=_client_ip(request),
        detail=json.dumps({"ok": result.get("ok"), "to": to or None}),
        after=result,
    )
    return {"status": "ok", "result": result}


# ── Audit logs ───────────────────────────────────────────────────────────────

@router.get("/audit-logs/export")
@require_any_permission("platform.audit.view", "platform.admin")
async def export_audit_logs(
    request: Request,
    category: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 5000,
):
    """CSV export of platform audit rows (filtered)."""
    conn = db.get_connection()
    ensure_ops_tables(conn)
    limit = max(1, min(int(limit or 5000), 20000))
    clauses = ["1=1"]
    params: list = []
    if category:
        clauses.append("action_category = ?")
        params.append(category)
    if status in ("success", "failure"):
        clauses.append("status = ?")
        params.append(status)
    if q and q.strip():
        clauses.append(
            "(action LIKE ? OR user_email LIKE ? OR detail LIKE ? OR tenant_id LIKE ?)"
        )
        like = f"%{q.strip()}%"
        params.extend([like, like, like, like])
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"""SELECT timestamp, user_email, user_id, tenant_id, action_category,
                  action, status, ip_address, detail, before_json, after_json
            FROM webclaw_platform_audit
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT ?""",
        [*params, limit],
    ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "timestamp",
            "user_email",
            "user_id",
            "tenant_id",
            "action_category",
            "action",
            "status",
            "ip_address",
            "detail",
            "before_json",
            "after_json",
        ]
    )
    for r in rows:
        if hasattr(r, "keys"):
            writer.writerow(
                [
                    r["timestamp"],
                    r["user_email"],
                    r["user_id"],
                    r["tenant_id"],
                    r["action_category"],
                    r["action"],
                    r["status"],
                    r["ip_address"],
                    r["detail"] or "",
                    r["before_json"] if "before_json" in r.keys() else "",
                    r["after_json"] if "after_json" in r.keys() else "",
                ]
            )
        else:
            writer.writerow(list(r))

    user_id, email = _actor(request)
    write_platform_audit(
        conn,
        user_id=user_id,
        user_email=email,
        tenant_id=None,
        action_category="audit",
        action="export_audit_csv",
        ip_address=_client_ip(request),
        detail=f"rows={len(rows)}",
    )
    payload = buf.getvalue().encode("utf-8")
    filename = f"platform-audit-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/audit-logs")
@require_any_permission("platform.audit.view", "platform.admin")
async def list_audit_logs(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    category: str | None = None,
    status: str | None = None,
    q: str | None = None,
):
    conn = db.get_connection()
    ensure_ops_tables(conn)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    clauses = ["1=1"]
    params: list = []
    if category:
        clauses.append("action_category = ?")
        params.append(category)
    if status in ("success", "failure"):
        clauses.append("status = ?")
        params.append(status)
    if q:
        clauses.append(
            "(user_email LIKE ? OR action LIKE ? OR tenant_id LIKE ? OR detail LIKE ? OR user_id LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, like, like])

    where = " AND ".join(clauses)
    total = conn.execute(
        f"SELECT COUNT(*) FROM webclaw_platform_audit WHERE {where}", params
    ).fetchone()[0]

    rows = conn.execute(
        f"""SELECT id, timestamp, user_id, user_email, tenant_id,
                   action_category, action, ip_address, status, detail,
                   before_json, after_json
            FROM webclaw_platform_audit
            WHERE {where}
            ORDER BY datetime(timestamp) DESC
            LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()

    def _parse_json_field(val):
        if not val:
            return None
        if isinstance(val, (dict, list)):
            return val
        try:
            return json.loads(val)
        except Exception:
            return val

    logs = []
    for r in rows:
        if hasattr(r, "keys"):
            before_raw = r["before_json"] if "before_json" in r.keys() else None
            after_raw = r["after_json"] if "after_json" in r.keys() else None
            logs.append(
                {
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "user_id": r["user_id"],
                    "user_email": r["user_email"],
                    "tenant_id": r["tenant_id"],
                    "action_category": r["action_category"],
                    "action": r["action"],
                    "ip_address": r["ip_address"],
                    "status": r["status"],
                    "detail": r["detail"],
                    "before": _parse_json_field(before_raw),
                    "after": _parse_json_field(after_raw),
                    "has_diff": bool(before_raw or after_raw),
                }
            )
        else:
            before_raw = r[10] if len(r) > 10 else None
            after_raw = r[11] if len(r) > 11 else None
            logs.append(
                {
                    "id": r[0],
                    "timestamp": r[1],
                    "user_id": r[2],
                    "user_email": r[3],
                    "tenant_id": r[4],
                    "action_category": r[5],
                    "action": r[6],
                    "ip_address": r[7],
                    "status": r[8],
                    "detail": r[9],
                    "before": _parse_json_field(before_raw),
                    "after": _parse_json_field(after_raw),
                    "has_diff": bool(before_raw or after_raw),
                }
            )

    # Also surface legacy skill audit_log rows (read-only) when platform table is empty-ish
    if total == 0 and _table_exists(conn, "audit_log"):
        try:
            legacy = conn.execute(
                """SELECT id, COALESCE(created_at, timestamp) as ts, user_id, skill, action, description
                   FROM audit_log ORDER BY COALESCE(created_at, timestamp) DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            for r in legacy:
                logs.append(
                    {
                        "id": str(r[0]),
                        "timestamp": r[1],
                        "user_id": r[2],
                        "user_email": None,
                        "tenant_id": None,
                        "action_category": "skill",
                        "action": f"{r[3]}/{r[4]}" if r[3] else str(r[4]),
                        "ip_address": None,
                        "status": "success",
                        "detail": r[5] or "",
                    }
                )
            total = len(logs)
        except Exception:
            pass

    return {"status": "ok", "logs": logs, "total": int(total), "limit": limit, "offset": offset}


def _table_exists(conn, name: str) -> bool:
    from db_access import table_exists

    return table_exists(conn, name)


# ── Announcements ────────────────────────────────────────────────────────────

class AnnouncementRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20000)
    channel: str = Field(default="banner")
    audience: str = Field(default="all")
    status: str = Field(default="draft")
    scheduled_at: str | None = None
    expires_at: str | None = None
    regions: list[str] | None = None

    @field_validator("channel")
    @classmethod
    def channel_ok(cls, v: str) -> str:
        value = v.strip().lower()
        if value not in {"banner", "email", "both"}:
            raise ValueError("channel must be banner, email, or both")
        return value

    @field_validator("audience")
    @classmethod
    def audience_ok(cls, v: str) -> str:
        value = v.strip().lower()
        if value not in {"all", "starter", "business", "enterprise", "professional"}:
            raise ValueError("invalid audience")
        return "business" if value == "professional" else value

    @field_validator("status")
    @classmethod
    def status_ok(cls, v: str) -> str:
        value = v.strip().lower()
        if value not in {"draft", "published", "archived"}:
            raise ValueError("invalid status")
        return value

    @field_validator("regions")
    @classmethod
    def regions_ok(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        out = []
        for item in v:
            code = str(item or "").strip().upper()
            if code and len(code) <= 8:
                out.append(code)
        return out


def _announcement_dict(row) -> dict:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        regions = _parse_regions(row["regions_json"] if "regions_json" in row.keys() else None)
        return {
            "id": row["id"] if "id" in row.keys() else None,
            "title": row["title"] if "title" in row.keys() else None,
            "body": row["body"] if "body" in row.keys() else None,
            "channel": row["channel"] if "channel" in row.keys() else None,
            "audience": row["audience"] if "audience" in row.keys() else None,
            "status": row["status"] if "status" in row.keys() else None,
            "published_at": row["published_at"] if "published_at" in row.keys() else None,
            "created_by": row["created_by"] if "created_by" in row.keys() else None,
            "created_by_email": row["created_by_email"] if "created_by_email" in row.keys() else None,
            "created_at": row["created_at"] if "created_at" in row.keys() else None,
            "updated_at": row["updated_at"] if "updated_at" in row.keys() else None,
            "scheduled_at": row["scheduled_at"] if "scheduled_at" in row.keys() else None,
            "expires_at": row["expires_at"] if "expires_at" in row.keys() else None,
            "regions": regions,
            "email_delivered_at": (
                row["email_delivered_at"] if "email_delivered_at" in row.keys() else None
            ),
        }
    # Tuple fallback — prefer SELECT * after migrations so trailing cols exist
    return {
        "id": row[0] if len(row) > 0 else None,
        "title": row[1] if len(row) > 1 else None,
        "body": row[2] if len(row) > 2 else None,
        "channel": row[3] if len(row) > 3 else None,
        "audience": row[4] if len(row) > 4 else None,
        "status": row[5] if len(row) > 5 else None,
        "published_at": row[6] if len(row) > 6 else None,
        "created_by": row[7] if len(row) > 7 else None,
        "created_by_email": row[8] if len(row) > 8 else None,
        "created_at": row[9] if len(row) > 9 else None,
        "updated_at": row[10] if len(row) > 10 else None,
        "scheduled_at": row[11] if len(row) > 11 else None,
        "expires_at": row[12] if len(row) > 12 else None,
        "regions": _parse_regions(row[13] if len(row) > 13 else None),
        "email_delivered_at": row[14] if len(row) > 14 else None,
    }


def _regions_json(regions: list[str] | None) -> str:
    return json.dumps(regions or [])


@router.get("/announcements")
@require_any_permission("platform.settings.manage", "platform.admin")
async def list_announcements(request: Request):
    conn = db.get_connection()
    ensure_ops_tables(conn)
    activate_due_announcements(conn)
    rows = conn.execute(
        """SELECT * FROM webclaw_announcement
           ORDER BY COALESCE(
             CAST(published_at AS TEXT),
             CAST(scheduled_at AS TEXT),
             CAST(created_at AS TEXT)
           ) DESC"""
    ).fetchall()
    return {"status": "ok", "announcements": [_announcement_dict(r) for r in rows]}


@router.post("/announcements")
@require_any_permission("platform.settings.manage", "platform.admin")
async def create_announcement(request: Request):
    try:
        body = await request.json()
        req = AnnouncementRequest(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    conn = db.get_connection()
    ensure_ops_tables(conn)
    user_id, email = _actor(request)
    now = _now()
    ann_id = str(uuid.uuid4())
    status = req.status
    published_at = now if status == "published" and not req.scheduled_at else None
    # Future schedule stays draft until the scheduler/activator publishes it.
    if req.scheduled_at and status == "published":
        try:
            sched = datetime.fromisoformat(req.scheduled_at.replace("Z", "+00:00"))
            if sched.tzinfo is None:
                sched = sched.replace(tzinfo=timezone.utc)
            if sched > datetime.now(timezone.utc):
                status = "draft"
                published_at = None
        except Exception:
            pass
    regions_json = _regions_json(req.regions)
    conn.execute(
        """INSERT INTO webclaw_announcement
           (id, title, body, channel, audience, status, published_at,
            created_by, created_by_email, created_at, updated_at,
            scheduled_at, expires_at, regions_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            ann_id, req.title, req.body, req.channel, req.audience, status,
            published_at, user_id, email, now, now,
            req.scheduled_at, req.expires_at, regions_json,
        ),
    )
    conn.commit()
    write_platform_audit(
        conn,
        user_id=user_id,
        user_email=email,
        tenant_id=None,
        action_category="announcement",
        action="create_announcement" if status == "draft" else "publish_announcement",
        ip_address=_client_ip(request),
        detail=req.title,
    )
    if status == "published":
        try:
            await deliver_announcement_emails(ann_id, conn)
        except Exception:
            pass
    row = conn.execute(
        "SELECT * FROM webclaw_announcement WHERE id = ?", (ann_id,)
    ).fetchone()
    return {"status": "ok", "announcement": _announcement_dict(row)}


@router.put("/announcements/{announcement_id}")
@require_any_permission("platform.settings.manage", "platform.admin")
async def update_announcement(announcement_id: str, request: Request):
    try:
        body = await request.json()
        req = AnnouncementRequest(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    conn = db.get_connection()
    ensure_ops_tables(conn)
    existing = conn.execute(
        "SELECT status, published_at FROM webclaw_announcement WHERE id = ?",
        (announcement_id,),
    ).fetchone()
    if not existing:
        return JSONResponse({"status": "error", "message": "Announcement not found"}, 404)

    prev_status = existing[0] if isinstance(existing, tuple) else existing["status"]
    prev_published = existing[1] if isinstance(existing, tuple) else existing["published_at"]
    status = req.status
    published_at = prev_published
    if req.scheduled_at and status == "published":
        try:
            sched = datetime.fromisoformat(req.scheduled_at.replace("Z", "+00:00"))
            if sched.tzinfo is None:
                sched = sched.replace(tzinfo=timezone.utc)
            if sched > datetime.now(timezone.utc):
                status = "draft"
                published_at = None
        except Exception:
            pass
    if status == "published" and prev_status != "published":
        published_at = _now()
    elif status != "published":
        published_at = prev_published if status == "archived" else None

    user_id, email = _actor(request)
    conn.execute(
        """UPDATE webclaw_announcement
           SET title=?, body=?, channel=?, audience=?, status=?, published_at=?,
               scheduled_at=?, expires_at=?, regions_json=?, updated_at=?,
               email_delivered_at = CASE
                 WHEN ? = 'published' AND ? != 'published' THEN NULL
                 ELSE email_delivered_at END
           WHERE id=?""",
        (
            req.title, req.body, req.channel, req.audience, status,
            published_at, req.scheduled_at, req.expires_at,
            _regions_json(req.regions), _now(),
            status, prev_status,
            announcement_id,
        ),
    )
    conn.commit()
    write_platform_audit(
        conn,
        user_id=user_id,
        user_email=email,
        tenant_id=None,
        action_category="announcement",
        action="publish_announcement" if status == "published" else "update_announcement",
        ip_address=_client_ip(request),
        detail=req.title,
    )
    if status == "published" and prev_status != "published":
        try:
            await deliver_announcement_emails(announcement_id, conn)
        except Exception:
            pass
    row = conn.execute(
        "SELECT * FROM webclaw_announcement WHERE id = ?", (announcement_id,)
    ).fetchone()
    return {"status": "ok", "announcement": _announcement_dict(row)}


@router.delete("/announcements/{announcement_id}")
@require_any_permission("platform.settings.manage", "platform.admin")
async def delete_announcement(announcement_id: str, request: Request):
    conn = db.get_connection()
    ensure_ops_tables(conn)
    row = conn.execute(
        "SELECT title FROM webclaw_announcement WHERE id = ?", (announcement_id,)
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "Announcement not found"}, 404)
    title = row[0] if isinstance(row, tuple) else row["title"]
    conn.execute("DELETE FROM webclaw_announcement WHERE id = ?", (announcement_id,))
    conn.commit()
    user_id, email = _actor(request)
    write_platform_audit(
        conn,
        user_id=user_id,
        user_email=email,
        tenant_id=None,
        action_category="announcement",
        action="delete_announcement",
        ip_address=_client_ip(request),
        detail=str(title),
    )
    return {"status": "ok", "message": "Deleted"}


# ── Security policy ──────────────────────────────────────────────────────────

class SecurityPolicyUpdate(BaseModel):
    enforce_2fa: bool | None = None
    session_timeout_minutes: int | None = Field(default=None, ge=5, le=1440)
    password_min_length: int | None = Field(default=None, ge=6, le=128)
    lockout_after_failures: int | None = Field(default=None, ge=1, le=50)
    maintenance_mode: bool | None = None
    maintenance_message: str | None = Field(default=None, max_length=500)
    auto_backup_enabled: bool | None = None
    auto_backup_interval_hours: int | None = Field(default=None, ge=1, le=168)
    offsite_backup_enabled: bool | None = None
    offsite_backup_url: str | None = Field(default=None, max_length=500)
    offsite_backup_token: str | None = Field(default=None, max_length=500)
    ip_allowlist_enabled: bool | None = None
    ip_allowlist: list[str] | None = None


def _parse_ip_allowlist(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        pass
    # newline / comma separated fallback
    parts = str(raw).replace(",", "\n").splitlines()
    return [p.strip() for p in parts if p.strip()]


def _load_security(conn: DbConn) -> dict:
    ensure_ops_tables(conn)
    row = conn.execute(
        """SELECT enforce_2fa, session_timeout_minutes, password_min_length,
                  lockout_after_failures, maintenance_mode, maintenance_message,
                  auto_backup_enabled, auto_backup_interval_hours, last_auto_backup_at,
                  offsite_backup_enabled, offsite_backup_url, offsite_backup_token,
                  ip_allowlist_enabled, ip_allowlist_json
           FROM webclaw_security_policy WHERE id = 'default'"""
    ).fetchone()
    if not row:
        return dict(DEFAULT_SECURITY)
    if hasattr(row, "keys"):
        keys = set(row.keys())
        token = (
            row["offsite_backup_token"]
            if "offsite_backup_token" in keys and row["offsite_backup_token"]
            else ""
        )
        allow_raw = row["ip_allowlist_json"] if "ip_allowlist_json" in keys else None
        return {
            "enforce_2fa": bool(row["enforce_2fa"]),
            "session_timeout_minutes": int(row["session_timeout_minutes"] or 15),
            "password_min_length": int(row["password_min_length"] or 8),
            "lockout_after_failures": int(row["lockout_after_failures"] or 5),
            "maintenance_mode": bool(row["maintenance_mode"]) if "maintenance_mode" in keys else False,
            "maintenance_message": (
                row["maintenance_message"]
                if "maintenance_message" in keys and row["maintenance_message"]
                else DEFAULT_SECURITY["maintenance_message"]
            ),
            "auto_backup_enabled": bool(row["auto_backup_enabled"])
            if "auto_backup_enabled" in keys
            else False,
            "auto_backup_interval_hours": int(
                row["auto_backup_interval_hours"]
                if "auto_backup_interval_hours" in keys and row["auto_backup_interval_hours"]
                else 24
            ),
            "last_auto_backup_at": row["last_auto_backup_at"]
            if "last_auto_backup_at" in keys
            else None,
            "offsite_backup_enabled": bool(row["offsite_backup_enabled"])
            if "offsite_backup_enabled" in keys
            else False,
            "offsite_backup_url": (
                row["offsite_backup_url"]
                if "offsite_backup_url" in keys and row["offsite_backup_url"]
                else ""
            ),
            "offsite_backup_token": token,
            "offsite_backup_token_set": bool(token),
            "ip_allowlist_enabled": bool(row["ip_allowlist_enabled"])
            if "ip_allowlist_enabled" in keys
            else False,
            "ip_allowlist": _parse_ip_allowlist(allow_raw),
        }
    allow_raw = row[13] if len(row) > 13 else None
    return {
        "enforce_2fa": bool(row[0]),
        "session_timeout_minutes": int(row[1] or 15),
        "password_min_length": int(row[2] or 8),
        "lockout_after_failures": int(row[3] or 5),
        "maintenance_mode": bool(row[4]) if len(row) > 4 else False,
        "maintenance_message": row[5]
        if len(row) > 5 and row[5]
        else DEFAULT_SECURITY["maintenance_message"],
        "auto_backup_enabled": bool(row[6]) if len(row) > 6 else False,
        "auto_backup_interval_hours": int(row[7] or 24) if len(row) > 7 else 24,
        "last_auto_backup_at": row[8] if len(row) > 8 else None,
        "offsite_backup_enabled": bool(row[9]) if len(row) > 9 else False,
        "offsite_backup_url": row[10] if len(row) > 10 and row[10] else "",
        "offsite_backup_token": row[11] if len(row) > 11 and row[11] else "",
        "offsite_backup_token_set": bool(row[11]) if len(row) > 11 and row[11] else False,
        "ip_allowlist_enabled": bool(row[12]) if len(row) > 12 else False,
        "ip_allowlist": _parse_ip_allowlist(allow_raw),
    }


@router.get("/security-policy")
@require_any_permission("platform.security.manage", "platform.admin")
async def get_security_policy(request: Request):
    conn = db.get_connection()
    policy = _load_security(conn)
    policy["offsite_backup_token_set"] = bool(policy.get("offsite_backup_token"))
    policy["offsite_backup_token"] = ""
    return {"status": "ok", "policy": policy}


@router.get("/maintenance-status")
async def maintenance_status(request: Request):
    """Public-ish: whether hotel logins are blocked for maintenance."""
    return {"status": "ok", **get_maintenance_state()}


@router.put("/security-policy")
@require_any_permission("platform.security.manage", "platform.admin")
async def update_security_policy(request: Request):
    try:
        body = await request.json()
        req = SecurityPolicyUpdate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    conn = db.get_connection()
    current = _load_security(conn)
    previous_token = current.get("offsite_backup_token") or ""
    before_snap = {
        k: current.get(k)
        for k in (
            "enforce_2fa",
            "session_timeout_minutes",
            "lockout_after_failures",
            "maintenance_mode",
            "auto_backup_enabled",
            "auto_backup_interval_hours",
            "offsite_backup_enabled",
            "offsite_backup_url",
            "ip_allowlist_enabled",
            "ip_allowlist",
        )
    }
    patch = req.model_dump(exclude_none=True)
    current.update(patch)
    user_id, email = _actor(request)
    # Preserve existing token when client sends empty string
    if "offsite_backup_token" in patch and not (patch.get("offsite_backup_token") or "").strip():
        current["offsite_backup_token"] = previous_token

    allowlist = _parse_ip_allowlist(current.get("ip_allowlist"))
    current["ip_allowlist"] = allowlist

    conn.execute(
        """INSERT INTO webclaw_security_policy
           (id, enforce_2fa, session_timeout_minutes, password_min_length,
            lockout_after_failures, maintenance_mode, maintenance_message,
            auto_backup_enabled, auto_backup_interval_hours, last_auto_backup_at,
            offsite_backup_enabled, offsite_backup_url, offsite_backup_token,
            ip_allowlist_enabled, ip_allowlist_json,
            updated_at, updated_by)
           VALUES ('default', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             enforce_2fa = excluded.enforce_2fa,
             session_timeout_minutes = excluded.session_timeout_minutes,
             password_min_length = excluded.password_min_length,
             lockout_after_failures = excluded.lockout_after_failures,
             maintenance_mode = excluded.maintenance_mode,
             maintenance_message = excluded.maintenance_message,
             auto_backup_enabled = excluded.auto_backup_enabled,
             auto_backup_interval_hours = excluded.auto_backup_interval_hours,
             last_auto_backup_at = COALESCE(
               webclaw_security_policy.last_auto_backup_at, excluded.last_auto_backup_at
             ),
             offsite_backup_enabled = excluded.offsite_backup_enabled,
             offsite_backup_url = excluded.offsite_backup_url,
             offsite_backup_token = excluded.offsite_backup_token,
             ip_allowlist_enabled = excluded.ip_allowlist_enabled,
             ip_allowlist_json = excluded.ip_allowlist_json,
             updated_at = excluded.updated_at,
             updated_by = excluded.updated_by""",
        (
            1 if current["enforce_2fa"] else 0,
            int(current["session_timeout_minutes"]),
            int(current["password_min_length"]),
            int(current["lockout_after_failures"]),
            1 if current.get("maintenance_mode") else 0,
            current.get("maintenance_message") or DEFAULT_SECURITY["maintenance_message"],
            1 if current.get("auto_backup_enabled") else 0,
            int(current.get("auto_backup_interval_hours") or 24),
            current.get("last_auto_backup_at"),
            1 if current.get("offsite_backup_enabled") else 0,
            (current.get("offsite_backup_url") or "").strip(),
            (current.get("offsite_backup_token") or "").strip(),
            1 if current.get("ip_allowlist_enabled") else 0,
            json.dumps(allowlist),
            _now(),
            email or user_id,
        ),
    )
    conn.commit()
    after_snap = {
        k: current.get(k)
        for k in (
            "enforce_2fa",
            "session_timeout_minutes",
            "lockout_after_failures",
            "maintenance_mode",
            "auto_backup_enabled",
            "auto_backup_interval_hours",
            "offsite_backup_enabled",
            "offsite_backup_url",
            "ip_allowlist_enabled",
            "ip_allowlist",
        )
    }
    write_platform_audit(
        conn,
        user_id=user_id,
        user_email=email,
        tenant_id=None,
        action_category="security",
        action="update_security_policy",
        ip_address=_client_ip(request),
        detail=json.dumps(after_snap),
        before=before_snap,
        after=after_snap,
    )
    out = dict(current)
    out["offsite_backup_token_set"] = bool(out.get("offsite_backup_token"))
    out["offsite_backup_token"] = ""  # never echo secret
    out["ip_allowlist"] = allowlist
    return {"status": "ok", "policy": out}


# ── Backups ──────────────────────────────────────────────────────────────────

@router.get("/backups")
@require_any_permission("platform.security.manage", "platform.admin")
async def list_backups(request: Request):
    conn = db.get_connection()
    ensure_ops_tables(conn)
    rows = conn.execute(
        """SELECT id, filename, path, size_bytes, status, created_by,
                  created_by_email, created_at, downloaded_at, error
           FROM webclaw_backup_log
           ORDER BY created_at DESC
           LIMIT 100"""
    ).fetchall()
    backups = []
    for r in rows:
        backups.append(
            {
                "id": r[0] if isinstance(r, tuple) else r["id"],
                "filename": r[1] if isinstance(r, tuple) else r["filename"],
                "size_bytes": r[3] if isinstance(r, tuple) else r["size_bytes"],
                "status": r[4] if isinstance(r, tuple) else r["status"],
                "created_by": r[5] if isinstance(r, tuple) else r["created_by"],
                "created_by_email": r[6] if isinstance(r, tuple) else r["created_by_email"],
                "created_at": r[7] if isinstance(r, tuple) else r["created_at"],
                "downloaded_at": r[8] if isinstance(r, tuple) else r["downloaded_at"],
                "error": r[9] if isinstance(r, tuple) else r["error"],
            }
        )
    return {"status": "ok", "backups": backups}


@router.post("/backups/snapshot")
@require_any_permission("platform.security.manage", "platform.admin")
async def create_backup_snapshot(request: Request):
    conn = db.get_connection()
    ensure_ops_tables(conn)
    user_id, email = _actor(request)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"webclaw-snapshot-{stamp}.sql"
    dest = _backups_dir() / filename
    backup_id = str(uuid.uuid4())
    try:
        _pg_dump_platform(dest)
        size = dest.stat().st_size
        policy = _load_security(conn)
        offsite = _maybe_offsite_copy(dest, policy)
        conn.execute(
            """INSERT INTO webclaw_backup_log
               (id, filename, path, size_bytes, status, created_by, created_by_email, created_at)
               VALUES (?, ?, ?, ?, 'completed', ?, ?, ?)""",
            (backup_id, filename, str(dest), size, user_id, email, _now()),
        )
        conn.commit()
        write_platform_audit(
            conn,
            user_id=user_id,
            user_email=email,
            tenant_id=None,
            action_category="backup",
            action="create_snapshot",
            ip_address=_client_ip(request),
            detail=json.dumps({"filename": filename, "offsite": offsite}),
        )
        return {
            "status": "ok",
            "backup": {
                "id": backup_id,
                "filename": filename,
                "size_bytes": size,
                "status": "completed",
                "created_at": _now(),
                "offsite": offsite,
            },
        }
    except Exception as e:
        conn.execute(
            """INSERT INTO webclaw_backup_log
               (id, filename, path, size_bytes, status, created_by, created_by_email, created_at, error)
               VALUES (?, ?, ?, 0, 'failed', ?, ?, ?, ?)""",
            (backup_id, filename, str(dest), user_id, email, _now(), str(e)),
        )
        conn.commit()
        write_platform_audit(
            conn,
            user_id=user_id,
            user_email=email,
            tenant_id=None,
            action_category="backup",
            action="create_snapshot",
            ip_address=_client_ip(request),
            status="failure",
            detail=str(e),
        )
        return JSONResponse({"status": "error", "message": str(e)}, 500)


def _pg_restore_platform(src: Path) -> None:
    """Apply a plain SQL dump into the platform database via psql."""
    psql = shutil.which("psql")
    if not psql:
        raise RuntimeError(
            "psql not found on PATH. Install PostgreSQL client tools to restore from the console."
        )
    if not src.exists():
        raise RuntimeError("Backup file not found on disk")
    url = _platform_postgres_url()
    parsed = urlparse(url)
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    cmd = [psql, "--set", "ON_ERROR_STOP=1", "-f", str(src)]
    if parsed.hostname:
        cmd.append(f"--host={parsed.hostname}")
    if parsed.port:
        cmd.append(f"--port={parsed.port}")
    if parsed.username:
        cmd.append(f"--username={unquote(parsed.username)}")
    dbname = unquote((parsed.path or "/pms").lstrip("/")) or "pms"
    cmd.append(dbname)
    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "psql restore failed").strip()
        raise RuntimeError(err)


@router.post("/backups/{backup_id}/restore")
@require_any_permission("platform.security.manage", "platform.admin")
async def restore_backup(backup_id: str, request: Request):
    """Restore a completed snapshot. Requires confirm='RESTORE' in the body."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict) or str(body.get("confirm") or "") != "RESTORE":
        return JSONResponse(
            {
                "status": "error",
                "message": "Restore requires JSON body {\"confirm\": \"RESTORE\"}",
            },
            400,
        )

    conn = db.get_connection()
    ensure_ops_tables(conn)
    row = conn.execute(
        "SELECT filename, path, status FROM webclaw_backup_log WHERE id = ?",
        (backup_id,),
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "Backup not found"}, 404)
    filename = row[0] if isinstance(row, tuple) else row["filename"]
    path = row[1] if isinstance(row, tuple) else row["path"]
    status = row[2] if isinstance(row, tuple) else row["status"]
    if status != "completed" or not path:
        return JSONResponse({"status": "error", "message": "Backup file unavailable"}, 404)

    user_id, email = _actor(request)
    try:
        _pg_restore_platform(Path(path))
    except Exception as e:
        write_platform_audit(
            conn,
            user_id=user_id,
            user_email=email,
            tenant_id=None,
            action_category="backup",
            action="restore_snapshot",
            ip_address=_client_ip(request),
            status="failure",
            detail=f"{filename}: {e}",
        )
        return JSONResponse({"status": "error", "message": str(e)}, 500)

    write_platform_audit(
        conn,
        user_id=user_id,
        user_email=email,
        tenant_id=None,
        action_category="backup",
        action="restore_snapshot",
        ip_address=_client_ip(request),
        detail=filename,
    )
    return {
        "status": "ok",
        "message": (
            f"Restored {filename}. Restart the API if schema or auth secrets changed."
        ),
        "filename": filename,
    }


@router.get("/backups/{backup_id}/download")
@require_any_permission("platform.security.manage", "platform.admin")
async def download_backup(backup_id: str, request: Request):
    conn = db.get_connection()
    ensure_ops_tables(conn)
    row = conn.execute(
        "SELECT filename, path, status FROM webclaw_backup_log WHERE id = ?",
        (backup_id,),
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "Backup not found"}, 404)
    filename = row[0] if isinstance(row, tuple) else row["filename"]
    path = row[1] if isinstance(row, tuple) else row["path"]
    status = row[2] if isinstance(row, tuple) else row["status"]
    if status != "completed" or not path or not Path(path).exists():
        return JSONResponse({"status": "error", "message": "Backup file unavailable"}, 404)

    conn.execute(
        "UPDATE webclaw_backup_log SET downloaded_at = ? WHERE id = ?",
        (_now(), backup_id),
    )
    conn.commit()
    user_id, email = _actor(request)
    write_platform_audit(
        conn,
        user_id=user_id,
        user_email=email,
        tenant_id=None,
        action_category="backup",
        action="download_snapshot",
        ip_address=_client_ip(request),
        detail=filename,
    )
    return FileResponse(path, filename=filename, media_type="application/octet-stream")


# ── Support tickets ───────────────────────────────────────────────────────────

class SupportTicketCreate(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=80)
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(default="", max_length=8000)
    priority: str = Field(default="normal")
    requester_email: str | None = Field(default=None, max_length=200)
    hotel_name: str | None = Field(default=None, max_length=200)

    @field_validator("priority")
    @classmethod
    def priority_ok(cls, v: str) -> str:
        allowed = {"low", "normal", "high", "urgent"}
        value = (v or "normal").lower()
        if value not in allowed:
            raise ValueError(f"priority must be one of {sorted(allowed)}")
        return value


class SupportTicketUpdate(BaseModel):
    subject: str | None = Field(default=None, min_length=3, max_length=200)
    body: str | None = Field(default=None, max_length=8000)
    status: str | None = None
    priority: str | None = None
    assignee_email: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=4000)

    @field_validator("status")
    @classmethod
    def status_ok(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"open", "in_progress", "waiting", "resolved", "closed"}
        value = v.lower()
        if value not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return value

    @field_validator("priority")
    @classmethod
    def priority_ok(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"low", "normal", "high", "urgent"}
        value = v.lower()
        if value not in allowed:
            raise ValueError(f"priority must be one of {sorted(allowed)}")
        return value


def _load_ticket_notes(conn: DbConn, ticket_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id, ticket_id, body, author_email, author_id, created_at
           FROM webclaw_support_ticket_note
           WHERE ticket_id = ?
           ORDER BY created_at ASC""",
        (ticket_id,),
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r[0] if isinstance(r, tuple) else r["id"],
                "ticket_id": r[1] if isinstance(r, tuple) else r["ticket_id"],
                "body": r[2] if isinstance(r, tuple) else r["body"],
                "author_email": r[3] if isinstance(r, tuple) else r["author_email"],
                "author_id": r[4] if isinstance(r, tuple) else r["author_id"],
                "created_at": r[5] if isinstance(r, tuple) else r["created_at"],
            }
        )
    return out


def _resolve_hotel_name(tenant_id: str, fallback: str | None = None) -> str | None:
    if fallback:
        return fallback
    erp = db.get_skill_db("erpclaw")
    if not erp:
        return None
    try:
        row = erp.execute(
            "SELECT hotel_name FROM pms_tenant WHERE id = ? OR company_id = ?",
            (tenant_id, tenant_id),
        ).fetchone()
        if not row:
            return None
        return row[0] if isinstance(row, tuple) else row["hotel_name"]
    except Exception:
        return None
    finally:
        erp.close()


@router.get("/support-tickets")
@require_any_permission("platform.support.manage", "platform.admin")
async def list_support_tickets(
    request: Request,
    status: str | None = None,
    tenant_id: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    conn = db.get_connection()
    ensure_ops_tables(conn)
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    clauses: list[str] = []
    params: list = []
    if status and status != "all":
        clauses.append("status = ?")
        params.append(status)
    if tenant_id:
        clauses.append("tenant_id = ?")
        params.append(tenant_id)
    if q and q.strip():
        clauses.append(
            "(subject LIKE ? OR hotel_name LIKE ? OR requester_email LIKE ? OR tenant_id LIKE ?)"
        )
        like = f"%{q.strip()}%"
        params.extend([like, like, like, like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = conn.execute(
        f"SELECT COUNT(*) FROM webclaw_support_ticket {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"""SELECT id, tenant_id, hotel_name, subject, body, status, priority,
                   requester_email, assignee_email, created_by, created_by_email,
                   created_at, updated_at, resolved_at,
                   sla_due_at, first_response_at, sla_breached
            FROM webclaw_support_ticket
            {where}
            ORDER BY
              CASE WHEN COALESCE(sla_breached, 0) = 1 THEN 0 ELSE 1 END,
              CASE priority
                WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                WHEN 'normal' THEN 2 ELSE 3 END,
              datetime(updated_at) DESC
            LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()
    return {
        "status": "ok",
        "tickets": [_ticket_dict(r) for r in rows],
        "total": int(total or 0),
    }


@router.post("/support-tickets")
@require_any_permission("platform.support.manage", "platform.admin")
async def create_support_ticket(request: Request):
    try:
        body = await request.json()
        req = SupportTicketCreate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    conn = db.get_connection()
    ensure_ops_tables(conn)
    user_id, email = _actor(request)
    now = _now()
    ticket_id = str(uuid.uuid4())
    hotel_name = _resolve_hotel_name(req.tenant_id, req.hotel_name)
    sla_due = _sla_due_at(req.priority, now)
    conn.execute(
        """INSERT INTO webclaw_support_ticket
           (id, tenant_id, hotel_name, subject, body, status, priority,
            requester_email, assignee_email, created_by, created_by_email,
            created_at, updated_at, resolved_at, sla_due_at, first_response_at, sla_breached)
           VALUES (?, ?, ?, ?, ?, 'open', ?, ?, NULL, ?, ?, ?, ?, NULL, ?, NULL, 0)""",
        (
            ticket_id,
            req.tenant_id,
            hotel_name,
            req.subject.strip(),
            req.body or "",
            req.priority,
            req.requester_email or email,
            user_id,
            email,
            now,
            now,
            sla_due,
        ),
    )
    conn.commit()
    write_platform_audit(
        conn,
        user_id=user_id,
        user_email=email,
        tenant_id=req.tenant_id,
        action_category="support",
        action="ticket_created",
        ip_address=_client_ip(request),
        detail=req.subject.strip(),
        after={"ticket_id": ticket_id, "priority": req.priority, "sla_due_at": sla_due},
    )
    row = conn.execute(
        """SELECT id, tenant_id, hotel_name, subject, body, status, priority,
                  requester_email, assignee_email, created_by, created_by_email,
                  created_at, updated_at, resolved_at,
                  sla_due_at, first_response_at, sla_breached
           FROM webclaw_support_ticket WHERE id = ?""",
        (ticket_id,),
    ).fetchone()
    return {"status": "ok", "ticket": _ticket_dict(row)}


@router.get("/support-tickets/{ticket_id}")
@require_any_permission("platform.support.manage", "platform.admin")
async def get_support_ticket(ticket_id: str, request: Request):
    conn = db.get_connection()
    ensure_ops_tables(conn)
    row = conn.execute(
        """SELECT id, tenant_id, hotel_name, subject, body, status, priority,
                  requester_email, assignee_email, created_by, created_by_email,
                  created_at, updated_at, resolved_at,
                  sla_due_at, first_response_at, sla_breached
           FROM webclaw_support_ticket WHERE id = ?""",
        (ticket_id,),
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "Ticket not found"}, 404)
    notes = _load_ticket_notes(conn, ticket_id)
    return {"status": "ok", "ticket": _ticket_dict(row, notes)}


@router.patch("/support-tickets/{ticket_id}")
@require_any_permission("platform.support.manage", "platform.admin")
async def update_support_ticket(ticket_id: str, request: Request):
    try:
        body = await request.json()
        req = SupportTicketUpdate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)

    conn = db.get_connection()
    ensure_ops_tables(conn)
    row = conn.execute(
        """SELECT id, status, priority, first_response_at, sla_due_at
           FROM webclaw_support_ticket WHERE id = ?""",
        (ticket_id,),
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "Ticket not found"}, 404)

    before_status = row[1] if isinstance(row, tuple) else row["status"]
    first_resp = row[3] if isinstance(row, tuple) else row["first_response_at"]
    user_id, email = _actor(request)
    now = _now()
    updates: list[str] = ["updated_at = ?"]
    params: list = [now]
    if req.subject is not None:
        updates.append("subject = ?")
        params.append(req.subject.strip())
    if req.body is not None:
        updates.append("body = ?")
        params.append(req.body)
    if req.priority is not None:
        updates.append("priority = ?")
        params.append(req.priority)
        updates.append("sla_due_at = ?")
        created = None
        try:
            created_row = conn.execute(
                "SELECT created_at FROM webclaw_support_ticket WHERE id = ?",
                (ticket_id,),
            ).fetchone()
            created = (
                created_row[0]
                if isinstance(created_row, tuple)
                else created_row["created_at"]
            )
        except Exception:
            created = None
        params.append(_sla_due_at(req.priority, created))
        updates.append("sla_breached = 0")
    if req.assignee_email is not None:
        updates.append("assignee_email = ?")
        params.append(req.assignee_email or None)
    if req.status is not None:
        updates.append("status = ?")
        params.append(req.status)
        if req.status in ("resolved", "closed"):
            updates.append("resolved_at = ?")
            params.append(now)
        elif req.status in ("open", "in_progress", "waiting"):
            updates.append("resolved_at = NULL")

    # First staff response when a note is added or status moves past open
    if not first_resp and (
        (req.note and req.note.strip())
        or (req.status and req.status != "open" and before_status == "open")
    ):
        updates.append("first_response_at = ?")
        params.append(now)

    params.append(ticket_id)
    conn.execute(
        f"UPDATE webclaw_support_ticket SET {', '.join(updates)} WHERE id = ?",
        params,
    )

    if req.note and req.note.strip():
        conn.execute(
            """INSERT INTO webclaw_support_ticket_note
               (id, ticket_id, body, author_email, author_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), ticket_id, req.note.strip(), email, user_id, now),
        )

    conn.commit()
    write_platform_audit(
        conn,
        user_id=user_id,
        user_email=email,
        tenant_id=None,
        action_category="support",
        action="ticket_updated",
        ip_address=_client_ip(request),
        detail=ticket_id,
        before={"status": before_status},
        after={
            "status": req.status or before_status,
            "priority": req.priority,
            "note_added": bool(req.note and req.note.strip()),
        },
    )
    full = conn.execute(
        """SELECT id, tenant_id, hotel_name, subject, body, status, priority,
                  requester_email, assignee_email, created_by, created_by_email,
                  created_at, updated_at, resolved_at,
                  sla_due_at, first_response_at, sla_breached
           FROM webclaw_support_ticket WHERE id = ?""",
        (ticket_id,),
    ).fetchone()
    notes = _load_ticket_notes(conn, ticket_id)
    return {"status": "ok", "ticket": _ticket_dict(full, notes)}


class SupportMacroCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    body: str = Field(min_length=1, max_length=8000)
    category: str = Field(default="general", max_length=60)


class SupportMacroUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    body: str | None = Field(default=None, min_length=1, max_length=8000)
    category: str | None = Field(default=None, max_length=60)
    is_active: bool | None = None


@router.get("/support-macros")
@require_any_permission("platform.support.manage", "platform.admin")
async def list_support_macros(request: Request, include_inactive: bool = False):
    conn = db.get_connection()
    ensure_ops_tables(conn)
    if include_inactive:
        rows = conn.execute(
            """SELECT id, name, body, category, is_active, created_at, updated_at, updated_by
               FROM webclaw_support_macro ORDER BY name"""
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, name, body, category, is_active, created_at, updated_at, updated_by
               FROM webclaw_support_macro WHERE is_active = 1 ORDER BY name"""
        ).fetchall()
    macros = []
    for r in rows:
        macros.append(
            {
                "id": r[0] if isinstance(r, tuple) else r["id"],
                "name": r[1] if isinstance(r, tuple) else r["name"],
                "body": r[2] if isinstance(r, tuple) else r["body"],
                "category": r[3] if isinstance(r, tuple) else r["category"],
                "is_active": bool(r[4] if isinstance(r, tuple) else r["is_active"]),
                "created_at": r[5] if isinstance(r, tuple) else r["created_at"],
                "updated_at": r[6] if isinstance(r, tuple) else r["updated_at"],
                "updated_by": r[7] if isinstance(r, tuple) else r["updated_by"],
            }
        )
    return {"status": "ok", "macros": macros}


@router.post("/support-macros")
@require_any_permission("platform.support.manage", "platform.admin")
async def create_support_macro(request: Request):
    try:
        body = await request.json()
        req = SupportMacroCreate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_ops_tables(conn)
    user_id, email = _actor(request)
    mid = str(uuid.uuid4())
    now = _now()
    conn.execute(
        """INSERT INTO webclaw_support_macro
           (id, name, body, category, is_active, created_at, updated_at, updated_by)
           VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
        (mid, req.name.strip(), req.body, req.category or "general", now, now, email or user_id),
    )
    conn.commit()
    return {
        "status": "ok",
        "macro": {
            "id": mid,
            "name": req.name.strip(),
            "body": req.body,
            "category": req.category or "general",
            "is_active": True,
        },
    }


@router.patch("/support-macros/{macro_id}")
@require_any_permission("platform.support.manage", "platform.admin")
async def update_support_macro(macro_id: str, request: Request):
    try:
        body = await request.json()
        req = SupportMacroUpdate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_ops_tables(conn)
    if not conn.execute(
        "SELECT 1 FROM webclaw_support_macro WHERE id = ?", (macro_id,)
    ).fetchone():
        return JSONResponse({"status": "error", "message": "Macro not found"}, 404)
    user_id, email = _actor(request)
    sets = ["updated_at = ?", "updated_by = ?"]
    params: list = [_now(), email or user_id]
    if req.name is not None:
        sets.append("name = ?")
        params.append(req.name.strip())
    if req.body is not None:
        sets.append("body = ?")
        params.append(req.body)
    if req.category is not None:
        sets.append("category = ?")
        params.append(req.category)
    if req.is_active is not None:
        sets.append("is_active = ?")
        params.append(1 if req.is_active else 0)
    params.append(macro_id)
    conn.execute(
        f"UPDATE webclaw_support_macro SET {', '.join(sets)} WHERE id = ?", params
    )
    conn.commit()
    return {"status": "ok"}


@router.post("/support-tickets/{ticket_id}/apply-macro")
@require_any_permission("platform.support.manage", "platform.admin")
async def apply_support_macro(ticket_id: str, request: Request):
    try:
        body = await request.json()
        macro_id = str((body or {}).get("macro_id") or "").strip()
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    if not macro_id:
        return JSONResponse({"status": "error", "message": "macro_id required"}, 400)
    conn = db.get_connection()
    ensure_ops_tables(conn)
    ticket = conn.execute(
        "SELECT id, first_response_at FROM webclaw_support_ticket WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    if not ticket:
        return JSONResponse({"status": "error", "message": "Ticket not found"}, 404)
    macro = conn.execute(
        "SELECT id, name, body FROM webclaw_support_macro WHERE id = ? AND is_active = 1",
        (macro_id,),
    ).fetchone()
    if not macro:
        return JSONResponse({"status": "error", "message": "Macro not found"}, 404)
    user_id, email = _actor(request)
    now = _now()
    body_text = macro[2] if isinstance(macro, tuple) else macro["body"]
    conn.execute(
        """INSERT INTO webclaw_support_ticket_note
           (id, ticket_id, body, author_email, author_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), ticket_id, body_text, email, user_id, now),
    )
    first_resp = ticket[1] if isinstance(ticket, tuple) else ticket["first_response_at"]
    if not first_resp:
        conn.execute(
            """UPDATE webclaw_support_ticket
               SET first_response_at = ?, updated_at = ?, status = CASE
                 WHEN status = 'open' THEN 'in_progress' ELSE status END
               WHERE id = ?""",
            (now, now, ticket_id),
        )
    else:
        conn.execute(
            "UPDATE webclaw_support_ticket SET updated_at = ? WHERE id = ?",
            (now, ticket_id),
        )
    conn.commit()
    write_platform_audit(
        conn,
        user_id=user_id,
        user_email=email,
        tenant_id=None,
        action_category="support",
        action="apply_macro",
        ip_address=_client_ip(request),
        detail=json.dumps({"ticket_id": ticket_id, "macro_id": macro_id}),
    )
    full = conn.execute(
        """SELECT id, tenant_id, hotel_name, subject, body, status, priority,
                  requester_email, assignee_email, created_by, created_by_email,
                  created_at, updated_at, resolved_at,
                  sla_due_at, first_response_at, sla_breached
           FROM webclaw_support_ticket WHERE id = ?""",
        (ticket_id,),
    ).fetchone()
    notes = _load_ticket_notes(conn, ticket_id)
    return {"status": "ok", "ticket": _ticket_dict(full, notes)}


# ── Ops health, alerts, job controls ─────────────────────────────────────────

class OpsJobUpdate(BaseModel):
    is_paused: bool


@router.get("/ops/health")
@require_any_permission("platform.security.manage", "platform.admin", "platform.tenants.view")
async def ops_health(request: Request):
    """Operator health: DB, jobs, open alerts."""
    conn = db.get_connection()
    ensure_ops_tables(conn)
    db_ok = True
    try:
        conn.execute("SELECT 1").fetchone()
    except Exception:
        db_ok = False
    jobs = conn.execute(
        """SELECT job_key, label, description, is_paused, last_run_at,
                  last_status, last_detail, updated_at
           FROM webclaw_ops_job ORDER BY job_key"""
    ).fetchall()
    open_alerts = int(
        conn.execute(
            "SELECT COUNT(*) FROM webclaw_ops_alert WHERE is_acked = 0"
        ).fetchone()[0]
        or 0
    )
    critical = int(
        conn.execute(
            """SELECT COUNT(*) FROM webclaw_ops_alert
               WHERE is_acked = 0 AND severity = 'critical'"""
        ).fetchone()[0]
        or 0
    )
    job_list = []
    any_paused = False
    for j in jobs:
        paused = bool(j[3] if isinstance(j, tuple) else j["is_paused"])
        if paused:
            any_paused = True
        job_list.append(
            {
                "job_key": j[0] if isinstance(j, tuple) else j["job_key"],
                "label": j[1] if isinstance(j, tuple) else j["label"],
                "description": j[2] if isinstance(j, tuple) else j["description"],
                "is_paused": paused,
                "last_run_at": j[4] if isinstance(j, tuple) else j["last_run_at"],
                "last_status": j[5] if isinstance(j, tuple) else j["last_status"],
                "last_detail": j[6] if isinstance(j, tuple) else j["last_detail"],
                "updated_at": j[7] if isinstance(j, tuple) else j["updated_at"],
            }
        )
    status = "operational"
    if not db_ok or critical:
        status = "degraded"
    elif any_paused or open_alerts:
        status = "attention"
    return {
        "status": "ok",
        "health": {
            "status": status,
            "database": "ok" if db_ok else "down",
            "open_alerts": open_alerts,
            "critical_alerts": critical,
            "jobs": job_list,
        },
    }


@router.get("/ops/alerts")
@require_any_permission("platform.security.manage", "platform.admin")
async def list_ops_alerts(
    request: Request,
    include_acked: bool = False,
    limit: int = 50,
):
    conn = db.get_connection()
    ensure_ops_tables(conn)
    limit = max(1, min(int(limit or 50), 200))
    if include_acked:
        rows = conn.execute(
            """SELECT id, severity, category, title, detail, is_acked,
                      created_at, acked_at, acked_by
               FROM webclaw_ops_alert
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, severity, category, title, detail, is_acked,
                      created_at, acked_at, acked_by
               FROM webclaw_ops_alert
               WHERE is_acked = 0
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    alerts = []
    for r in rows:
        alerts.append(
            {
                "id": r[0] if isinstance(r, tuple) else r["id"],
                "severity": r[1] if isinstance(r, tuple) else r["severity"],
                "category": r[2] if isinstance(r, tuple) else r["category"],
                "title": r[3] if isinstance(r, tuple) else r["title"],
                "detail": r[4] if isinstance(r, tuple) else r["detail"],
                "is_acked": bool(r[5] if isinstance(r, tuple) else r["is_acked"]),
                "created_at": r[6] if isinstance(r, tuple) else r["created_at"],
                "acked_at": r[7] if isinstance(r, tuple) else r["acked_at"],
                "acked_by": r[8] if isinstance(r, tuple) else r["acked_by"],
            }
        )
    return {"status": "ok", "alerts": alerts}


@router.post("/ops/alerts/{alert_id}/ack")
@require_any_permission("platform.security.manage", "platform.admin")
async def ack_ops_alert(alert_id: str, request: Request):
    conn = db.get_connection()
    ensure_ops_tables(conn)
    row = conn.execute(
        "SELECT id FROM webclaw_ops_alert WHERE id = ?", (alert_id,)
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "Alert not found"}, 404)
    user_id, email = _actor(request)
    conn.execute(
        """UPDATE webclaw_ops_alert
           SET is_acked = 1, acked_at = ?, acked_by = ?
           WHERE id = ?""",
        (_now(), email or user_id, alert_id),
    )
    conn.commit()
    return {"status": "ok"}


@router.get("/ops/jobs")
@require_any_permission("platform.security.manage", "platform.admin")
async def list_ops_jobs(request: Request):
    conn = db.get_connection()
    ensure_ops_tables(conn)
    rows = conn.execute(
        """SELECT job_key, label, description, is_paused, last_run_at,
                  last_status, last_detail, updated_at, updated_by
           FROM webclaw_ops_job ORDER BY job_key"""
    ).fetchall()
    return {
        "status": "ok",
        "jobs": [
            {
                "job_key": r[0] if isinstance(r, tuple) else r["job_key"],
                "label": r[1] if isinstance(r, tuple) else r["label"],
                "description": r[2] if isinstance(r, tuple) else r["description"],
                "is_paused": bool(r[3] if isinstance(r, tuple) else r["is_paused"]),
                "last_run_at": r[4] if isinstance(r, tuple) else r["last_run_at"],
                "last_status": r[5] if isinstance(r, tuple) else r["last_status"],
                "last_detail": r[6] if isinstance(r, tuple) else r["last_detail"],
                "updated_at": r[7] if isinstance(r, tuple) else r["updated_at"],
                "updated_by": r[8] if isinstance(r, tuple) else r["updated_by"],
            }
            for r in rows
        ],
    }


@router.put("/ops/jobs/{job_key}")
@require_any_permission("platform.security.manage", "platform.admin")
async def update_ops_job(job_key: str, request: Request):
    try:
        body = await request.json()
        req = OpsJobUpdate(**body)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, 400)
    conn = db.get_connection()
    ensure_ops_tables(conn)
    row = conn.execute(
        "SELECT job_key FROM webclaw_ops_job WHERE job_key = ?", (job_key,)
    ).fetchone()
    if not row:
        return JSONResponse({"status": "error", "message": "Unknown job"}, 404)
    user_id, email = _actor(request)
    conn.execute(
        """UPDATE webclaw_ops_job
           SET is_paused = ?, updated_at = ?, updated_by = ?
           WHERE job_key = ?""",
        (1 if req.is_paused else 0, _now(), email or user_id, job_key),
    )
    conn.commit()
    write_platform_audit(
        conn,
        user_id=user_id,
        user_email=email,
        tenant_id=None,
        action_category="ops",
        action="pause_job" if req.is_paused else "resume_job",
        ip_address=_client_ip(request),
        detail=job_key,
    )
    return {"status": "ok", "job_key": job_key, "is_paused": req.is_paused}


def build_tenant_export_package(tenant_id: str) -> dict:
    """Assemble a portable JSON snapshot for one hotel tenant."""
    package: dict = {
        "tenant_id": tenant_id,
        "generated_at": _now(),
        "hotel": None,
        "users": [],
        "billing": None,
        "rooms_count": 0,
        "reservations_sample": [],
    }
    erp = db.get_skill_db("erpclaw")
    if erp:
        try:
            row = erp.execute(
                "SELECT * FROM pms_tenant WHERE id = ? OR company_id = ?",
                (tenant_id, tenant_id),
            ).fetchone()
            if row:
                if hasattr(row, "keys"):
                    package["hotel"] = {k: row[k] for k in row.keys()}
                else:
                    package["hotel"] = {"id": tenant_id, "raw": True}
                tid = (
                    package["hotel"].get("id")
                    if isinstance(package["hotel"], dict)
                    else tenant_id
                )
                try:
                    package["rooms_count"] = int(
                        erp.execute(
                            "SELECT COUNT(*) FROM hospitalityclaw_room WHERE tenant_id = ?",
                            (tid,),
                        ).fetchone()[0]
                        or 0
                    )
                except Exception:
                    package["rooms_count"] = 0
                try:
                    res_rows = erp.execute(
                        """SELECT id, confirmation_code, status, check_in, check_out, guest_name
                           FROM hospitalityclaw_reservation
                           WHERE tenant_id = ?
                           ORDER BY check_in DESC LIMIT 50""",
                        (tid,),
                    ).fetchall()
                    for rr in res_rows:
                        if hasattr(rr, "keys"):
                            package["reservations_sample"].append(
                                {k: rr[k] for k in rr.keys()}
                            )
                except Exception:
                    pass
        finally:
            erp.close()

    conn = db.get_connection()
    cols = set()
    try:
        from db_access import table_columns

        cols = set(table_columns(conn, "webclaw_user") or [])
    except Exception:
        pass
    if "tenant_id" in cols:
        for u in conn.execute(
            """SELECT id, email, full_name, status, created_at
               FROM webclaw_user WHERE tenant_id = ?""",
            (tenant_id,),
        ).fetchall():
            package["users"].append(
                {
                    "id": u[0] if isinstance(u, tuple) else u["id"],
                    "email": u[1] if isinstance(u, tuple) else u["email"],
                    "full_name": u[2] if isinstance(u, tuple) else u["full_name"],
                    "status": u[3] if isinstance(u, tuple) else u["status"],
                    "created_at": u[4] if isinstance(u, tuple) else u["created_at"],
                }
            )
    try:
        bill = conn.execute(
            "SELECT * FROM webclaw_tenant_billing_override WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if bill and hasattr(bill, "keys"):
            package["billing"] = {k: bill[k] for k in bill.keys()}
    except Exception:
        pass
    return package


@router.get("/tenants/{tenant_id}/export")
@require_any_permission("platform.security.manage", "platform.tenants.manage", "platform.admin")
async def export_tenant_package(tenant_id: str, request: Request):
    """Download a JSON export package for a single hotel tenant."""
    package = build_tenant_export_package(tenant_id)
    if not package.get("hotel"):
        return JSONResponse({"status": "error", "message": "Tenant not found"}, 404)
    payload = json.dumps(package, indent=2, default=str).encode("utf-8")
    user_id, email = _actor(request)
    conn = db.get_connection()
    ensure_ops_tables(conn)
    write_platform_audit(
        conn,
        user_id=user_id,
        user_email=email,
        tenant_id=tenant_id,
        action_category="backup",
        action="tenant_export",
        ip_address=_client_ip(request),
        detail=tenant_id,
    )
    safe = tenant_id.replace("-", "")[:12]
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="tenant-export-{safe}.json"'
        },
    )

# -- Hotel-facing announcements (region + plan filtered) -----------------------

hotel_router = APIRouter(prefix="/api/v1/hotel", tags=["hotel-announcements"])


@hotel_router.get("/announcements/active")
async def hotel_active_announcements(request: Request):
    """Published banner announcements matching the hotel plan + country."""
    from auth.deps import get_current_user
    from admin_platform import TIER_TO_PLAN, _erp as erp_fn

    try:
        ctx = get_current_user(request)
    except Exception:
        return JSONResponse({"status": "error", "message": "Authentication required"}, 401)
    tenant_id = ctx.isolation_id or ctx.tenant_id
    if not tenant_id:
        return JSONResponse({"status": "error", "message": "Hotel tenant required"}, 400)

    plan_code = "starter"
    country = ""
    erp = erp_fn()
    if erp:
        try:
            row = erp.execute(
                "SELECT subscription_tier, country FROM pms_tenant WHERE id = ?",
                (tenant_id,),
            ).fetchone()
            if row:
                tier = row[0] if isinstance(row, tuple) else row["subscription_tier"]
                country = str((row[1] if isinstance(row, tuple) else row["country"]) or "")
                plan_code = TIER_TO_PLAN.get(str(tier or "").lower(), "starter")
        finally:
            erp.close()

    conn = db.get_connection()
    ensure_ops_tables(conn)
    activate_due_announcements(conn)
    rows = conn.execute(
        """SELECT * FROM webclaw_announcement
           WHERE status = 'published'
             AND channel IN ('banner', 'both')
           ORDER BY COALESCE(
             CAST(published_at AS TEXT),
             CAST(created_at AS TEXT)
           ) DESC
           LIMIT 50"""
    ).fetchall()
    matched = []
    for r in rows:
        d = _announcement_dict(r)
        if announcement_matches_tenant(
            audience=str(d.get("audience") or "all"),
            regions=list(d.get("regions") or []),
            plan_code=plan_code,
            country=country,
        ):
            matched.append(
                {
                    "id": d["id"],
                    "title": d["title"],
                    "body": d["body"],
                    "channel": d["channel"],
                    "audience": d["audience"],
                    "regions": d.get("regions") or [],
                    "published_at": d.get("published_at"),
                    "expires_at": d.get("expires_at"),
                }
            )
    return {
        "status": "ok",
        "tenant_id": tenant_id,
        "plan_code": plan_code,
        "country": country,
        "announcements": matched,
    }
