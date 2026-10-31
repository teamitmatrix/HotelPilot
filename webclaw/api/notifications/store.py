"""Notification settings persistence and delivery log."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import db
from db_access import DbConn
from notifications.defaults import merge_notification_settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_notification_tables(conn: DbConn | None = None) -> DbConn:
    from db_access import run_ddl

    c = conn or db.get_connection()
    run_ddl(
        c,
        """
        CREATE TABLE IF NOT EXISTS webclaw_notification_log (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            trigger_key     TEXT NOT NULL,
            channel         TEXT NOT NULL,
            reservation_id  TEXT,
            folio_id        TEXT,
            recipient       TEXT,
            status          TEXT NOT NULL DEFAULT 'sent'
                            CHECK(status IN ('sent','failed','skipped')),
            detail          TEXT,
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_notif_log_tenant
            ON webclaw_notification_log(tenant_id, trigger_key, created_at);
        CREATE INDEX IF NOT EXISTS idx_notif_log_res
            ON webclaw_notification_log(reservation_id, trigger_key, channel);
        """,
    )
    c.commit()
    return c


def already_sent(
    conn: DbConn,
    *,
    tenant_id: str,
    trigger_key: str,
    channel: str,
    reservation_id: str | None = None,
    folio_id: str | None = None,
) -> bool:
    ensure_notification_tables(conn)
    if reservation_id:
        row = conn.execute(
            """SELECT 1 FROM webclaw_notification_log
               WHERE tenant_id = ? AND trigger_key = ? AND channel = ?
                 AND reservation_id = ? AND status = 'sent'
               LIMIT 1""",
            (tenant_id, trigger_key, channel, reservation_id),
        ).fetchone()
        return bool(row)
    if folio_id:
        row = conn.execute(
            """SELECT 1 FROM webclaw_notification_log
               WHERE tenant_id = ? AND trigger_key = ? AND channel = ?
                 AND folio_id = ? AND status = 'sent'
               LIMIT 1""",
            (tenant_id, trigger_key, channel, folio_id),
        ).fetchone()
        return bool(row)
    return False


def log_notification(
    conn: DbConn,
    *,
    tenant_id: str,
    trigger_key: str,
    channel: str,
    status: str,
    reservation_id: str | None = None,
    folio_id: str | None = None,
    recipient: str | None = None,
    detail: str = "",
) -> None:
    ensure_notification_tables(conn)
    conn.execute(
        """INSERT INTO webclaw_notification_log
           (id, tenant_id, trigger_key, channel, reservation_id, folio_id,
            recipient, status, detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            tenant_id,
            trigger_key,
            channel,
            reservation_id,
            folio_id,
            recipient,
            status,
            (detail or "")[:2000],
            _now(),
        ),
    )
    conn.commit()


def _hotel_row(tenant_id: str):
    skill_db = db.get_skill_db("erpclaw") or db.get_skill_db("hospitalityclaw")
    if not skill_db:
        return None, None
    try:
        row = skill_db.execute(
            """SELECT id, name, currency, timezone, country, address, phone, email, settings_json
               FROM hospitalityclaw_hotel
               WHERE tenant_id = ?
               ORDER BY created_at ASC LIMIT 1""",
            (tenant_id,),
        ).fetchone()
        return skill_db, row
    except Exception:
        skill_db.close()
        return None, None


def load_hotel_notification_settings(tenant_id: str) -> dict:
    """Load merged notification trigger settings for a hotel tenant."""
    skill_db, row = _hotel_row(tenant_id)
    if not skill_db:
        return merge_notification_settings(None)
    try:
        raw_settings = {}
        if row:
            raw = row["settings_json"] if hasattr(row, "keys") else None
            if raw:
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                    if isinstance(parsed, dict):
                        raw_settings = parsed
                except Exception:
                    raw_settings = {}
        notif = raw_settings.get("notifications")
        return merge_notification_settings(notif if isinstance(notif, dict) else None)
    finally:
        skill_db.close()


def save_hotel_notification_settings(tenant_id: str, notifications: dict) -> dict:
    """Persist notifications block into hotel settings_json; return merged config."""
    merged = merge_notification_settings(notifications)
    skill_db, row = _hotel_row(tenant_id)
    if not skill_db or not row:
        if skill_db:
            skill_db.close()
        raise ValueError("Hotel not found for tenant")
    try:
        hotel_id = row["id"] if hasattr(row, "keys") else row[0]
        current: dict[str, Any] = {}
        raw = row["settings_json"] if hasattr(row, "keys") else None
        if raw:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, dict):
                    current = parsed
            except Exception:
                current = {}
        current["notifications"] = merged
        skill_db.execute(
            """UPDATE hospitalityclaw_hotel
               SET settings_json = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND tenant_id = ?""",
            (json.dumps(current), hotel_id, tenant_id),
        )
        skill_db.commit()
        return merged
    finally:
        skill_db.close()


def list_recent_logs(tenant_id: str, limit: int = 50) -> list[dict]:
    conn = ensure_notification_tables()
    rows = conn.execute(
        """SELECT id, trigger_key, channel, reservation_id, folio_id,
                  recipient, status, detail, created_at
           FROM webclaw_notification_log
           WHERE tenant_id = ?
           ORDER BY created_at DESC
           LIMIT ?""",
        (tenant_id, max(1, min(limit, 200))),
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r[0] if isinstance(r, tuple) else r["id"],
                "trigger_key": r[1] if isinstance(r, tuple) else r["trigger_key"],
                "channel": r[2] if isinstance(r, tuple) else r["channel"],
                "reservation_id": r[3] if isinstance(r, tuple) else r["reservation_id"],
                "folio_id": r[4] if isinstance(r, tuple) else r["folio_id"],
                "recipient": r[5] if isinstance(r, tuple) else r["recipient"],
                "status": r[6] if isinstance(r, tuple) else r["status"],
                "detail": r[7] if isinstance(r, tuple) else r["detail"],
                "created_at": r[8] if isinstance(r, tuple) else r["created_at"],
            }
        )
    return out
