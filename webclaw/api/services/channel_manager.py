"""Channel manager connector — rates, availability, and OTA bookings.

Outbound pushes speak a Channex-style JSON API (``user-api-key``) or a
SiteMinder-style JSON gateway (Bearer token). Inbound reservations arrive at
``POST /api/v1/integrations/ota/webhook``.

Availability for a night is the count of physical rooms of that type that
housekeeping and the reservation calendar can still sell, minus front-desk
bookings that have not been assigned a room number yet.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

import db

log = logging.getLogger("webclaw.channel_manager")

OTAS: dict[str, str] = {
    "booking_com": "Booking.com",
    "airbnb": "Airbnb",
    "expedia": "Expedia",
    "agoda": "Agoda",
}
PROVIDERS = ("channex", "siteminder")
DEFAULT_HORIZON_DAYS = 14
WEBHOOK_PATH = "/api/v1/integrations/ota/webhook"
SUPPORTED_OTA_HINT = "Booking.com, Airbnb, Expedia, or Agoda"

_OTA_ALIASES = {
    "bookingcom": "booking_com",
    "booking_com": "booking_com",
    "booking": "booking_com",
    "booking.com": "booking_com",
    "airbnb": "airbnb",
    "expedia": "expedia",
    "agoda": "agoda",
    "agoda.com": "agoda",
}

OUTBOUND_ACTIONS = frozenset({
    "update-room-status",
    "hospitality-update-room-status",
    "add-reservation",
    "hospitality-add-reservation",
    "cancel-reservation",
    "hospitality-cancel-reservation",
    "check-in",
    "hospitality-check-in",
    "check-out",
    "hospitality-check-out",
    "assign-room",
    "hospitality-assign-room",
    "add-room",
    "hospitality-add-room",
    "delete-room",
    "hospitality-delete-room",
    "update-room",
    "hospitality-update-room",
    "add-rate-plan",
    "hospitality-add-rate-plan",
    "update-rate-plan",
    "hospitality-update-rate-plan",
    "delete-rate-plan",
    "hospitality-delete-rate-plan",
    "update-room-type",
    "hospitality-update-room-type",
})

HttpTransport = Callable[[str, dict, dict], tuple[int, str]]

CHANNEL_DDL = """
CREATE TABLE IF NOT EXISTS hospitalityclaw_channel_connection (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    hotel_id TEXT NOT NULL,
    ota TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'channex',
    status TEXT NOT NULL DEFAULT 'disconnected',
    api_base_url TEXT,
    property_external_id TEXT,
    api_key TEXT,
    webhook_secret TEXT,
    last_success_at TEXT,
    last_error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, hotel_id, ota)
);
CREATE INDEX IF NOT EXISTS idx_hclaw_channel_tenant
    ON hospitalityclaw_channel_connection(tenant_id, hotel_id);

CREATE TABLE IF NOT EXISTS hospitalityclaw_channel_room_map (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    hotel_id TEXT NOT NULL,
    ota TEXT NOT NULL,
    ota_room_type_code TEXT NOT NULL,
    room_type_id TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, hotel_id, ota, ota_room_type_code)
);
CREATE INDEX IF NOT EXISTS idx_hclaw_channel_map_tenant
    ON hospitalityclaw_channel_room_map(tenant_id, hotel_id);

CREATE TABLE IF NOT EXISTS hospitalityclaw_channel_sync_log (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    hotel_id TEXT,
    ota TEXT,
    direction TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_hclaw_channel_log_tenant
    ON hospitalityclaw_channel_sync_log(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS hospitalityclaw_channel_booking (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    hotel_id TEXT NOT NULL,
    ota TEXT NOT NULL,
    external_id TEXT NOT NULL,
    reservation_id TEXT,
    reservation_ids TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, ota, external_id)
);

CREATE TABLE IF NOT EXISTS hospitalityclaw_channel_allotment (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    hotel_id TEXT NOT NULL,
    ota TEXT NOT NULL,
    room_type_id TEXT NOT NULL,
    stay_date TEXT NOT NULL,
    allotment INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, hotel_id, ota, room_type_id, stay_date)
);
CREATE INDEX IF NOT EXISTS idx_hclaw_channel_allotment
    ON hospitalityclaw_channel_allotment(tenant_id, hotel_id, ota, stay_date);
"""


def _pms_root() -> str:
    try:
        from env_loader import pms_root
        return pms_root()
    except Exception:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _hq():
    root = _pms_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    from hospitalityclaw.scripts import db_query as hq
    from hospitalityclaw.init_db import init_db
    return hq, init_db


def _erp():
    conn = db.get_skill_db("erpclaw") or db.get_skill_db("hospitalityclaw")
    if conn is None:
        raise RuntimeError("Hospitality database is not available")
    return conn


def _g(row, key, default=None):
    if row is None:
        return default
    try:
        if hasattr(row, "keys") and key in row.keys():
            return row[key]
    except Exception:
        pass
    try:
        return row[key]
    except Exception:
        return default


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_ota(value: str | None) -> str:
    raw = (value or "").strip().lower().replace(" ", "").replace("-", "_")
    if raw in _OTA_ALIASES:
        return _OTA_ALIASES[raw]
    compact = raw.replace(".", "").replace("_", "")
    return _OTA_ALIASES.get(compact, "")


def source_label(ota: str) -> str:
    return OTAS.get(ota, ota)


def webhook_signature(secret: str, raw: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def ensure_schema(conn) -> None:
    _, init_db = _hq()
    init_db(conn)
    for stmt in CHANNEL_DDL.split(";"):
        piece = stmt.strip()
        if piece:
            conn.execute(piece)
    # Soft-migrate commission % onto connections
    try:
        info = conn.execute(
            "PRAGMA table_info(hospitalityclaw_channel_connection)"
        ).fetchall()
        cols = set()
        for r in info:
            name = _g(r, "name")
            if name is None and isinstance(r, (tuple, list)) and len(r) > 1:
                name = r[1]
            if name:
                cols.add(str(name).lower())
        if "commission_percent" not in cols:
            conn.execute(
                "ALTER TABLE hospitalityclaw_channel_connection "
                "ADD COLUMN commission_percent REAL DEFAULT 0"
            )
    except Exception:
        try:
            conn.execute(
                "ALTER TABLE hospitalityclaw_channel_connection "
                "ADD COLUMN commission_percent REAL DEFAULT 0"
            )
        except Exception:
            pass
    conn.commit()


def _horizon() -> int:
    raw = os.environ.get("CHANNEL_SYNC_HORIZON_DAYS") or str(DEFAULT_HORIZON_DAYS)
    try:
        days = int(raw)
    except ValueError:
        days = DEFAULT_HORIZON_DAYS
    return max(1, min(days, 90))


def _money(value) -> str:
    try:
        return str(Decimal(str(value or "0")).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid money value: {value}")


def _log(conn, tenant_id, hotel_id, ota, direction, kind, status, message, payload=None) -> None:
    conn.execute(
        """INSERT INTO hospitalityclaw_channel_sync_log
           (id, tenant_id, hotel_id, ota, direction, kind, status, message, payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            tenant_id,
            hotel_id,
            ota,
            direction,
            kind,
            status,
            message[:500],
            json.dumps(payload)[:4000] if payload is not None else None,
        ),
    )


def _mask_connection(row) -> dict:
    key = str(_g(row, "api_key") or "")
    secret = str(_g(row, "webhook_secret") or "")
    try:
        commission = float(_g(row, "commission_percent") or 0)
    except Exception:
        commission = 0.0
    return {
        "id": _g(row, "id"),
        "hotel_id": _g(row, "hotel_id"),
        "property_id": _g(row, "hotel_id"),
        "ota": _g(row, "ota"),
        "ota_label": source_label(_g(row, "ota") or ""),
        "provider": _g(row, "provider"),
        "status": _g(row, "status"),
        "api_base_url": _g(row, "api_base_url") or "",
        "property_external_id": _g(row, "property_external_id") or "",
        "api_key_configured": bool(key),
        "api_key_last4": key[-4:] if len(key) >= 4 else "",
        "webhook_secret_configured": bool(secret),
        "commission_percent": commission,
        "last_success_at": _g(row, "last_success_at"),
        "last_error": _g(row, "last_error"),
    }


def _default_base(provider: str, explicit: str | None) -> str:
    if explicit and str(explicit).strip():
        return str(explicit).strip().rstrip("/")
    if provider == "channex":
        return (os.environ.get("CHANNEX_API_BASE") or "https://app.channex.io/api/v1").rstrip("/")
    return (os.environ.get("SITEMINDER_API_BASE") or "").rstrip("/")


def save_connection(tenant_id: str, body: dict) -> dict:
    ota = normalize_ota(body.get("ota"))
    if not ota:
        raise ValueError(f"ota must be {SUPPORTED_OTA_HINT}")
    provider = str(body.get("provider") or "channex").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError("provider must be channex or siteminder")
    hotel_id = str(body.get("hotel_id") or body.get("property_id") or "").strip()
    if not hotel_id:
        raise ValueError("hotel_id is required")
    status = str(body.get("status") or "connected").strip().lower()
    if status not in ("connected", "disconnected", "error"):
        raise ValueError("status must be connected or disconnected")

    conn = _erp()
    try:
        ensure_schema(conn)
        hotel = conn.execute(
            "SELECT id FROM hospitalityclaw_hotel WHERE id = ? AND tenant_id = ?",
            (hotel_id, tenant_id),
        ).fetchone()
        if not hotel:
            raise ValueError("Hotel not found for this property")
        existing = conn.execute(
            """SELECT * FROM hospitalityclaw_channel_connection
               WHERE tenant_id = ? AND hotel_id = ? AND ota = ?""",
            (tenant_id, hotel_id, ota),
        ).fetchone()
        api_key = body.get("api_key")
        webhook_secret = body.get("webhook_secret")
        if existing and (api_key is None or str(api_key).strip() == ""):
            api_key = _g(existing, "api_key")
        if existing and (webhook_secret is None or str(webhook_secret).strip() == ""):
            webhook_secret = _g(existing, "webhook_secret")
        base = _default_base(provider, body.get("api_base_url") or (_g(existing, "api_base_url") if existing else None))
        external = str(body.get("property_external_id") or (_g(existing, "property_external_id") if existing else "") or "").strip()
        if "commission_percent" in body and body.get("commission_percent") not in (None, ""):
            try:
                commission = max(0.0, min(100.0, float(body.get("commission_percent"))))
            except Exception:
                raise ValueError("commission_percent must be a number")
        elif existing:
            try:
                commission = float(_g(existing, "commission_percent") or 0)
            except Exception:
                commission = 0.0
        else:
            commission = 0.0
        if existing:
            conn.execute(
                """UPDATE hospitalityclaw_channel_connection
                   SET provider = ?, status = ?, api_base_url = ?, property_external_id = ?,
                       api_key = ?, webhook_secret = ?, commission_percent = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    provider, status, base, external, api_key or "", webhook_secret or "",
                    commission, _g(existing, "id"),
                ),
            )
            row_id = _g(existing, "id")
        else:
            row_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO hospitalityclaw_channel_connection
                   (id, tenant_id, hotel_id, ota, provider, status, api_base_url,
                    property_external_id, api_key, webhook_secret, commission_percent)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row_id, tenant_id, hotel_id, ota, provider, status, base,
                    external, api_key or "", webhook_secret or "", commission,
                ),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM hospitalityclaw_channel_connection WHERE id = ?",
            (row_id,),
        ).fetchone()
        return _mask_connection(row)
    finally:
        conn.close()


def save_mapping(tenant_id: str, body: dict) -> dict:
    ota = normalize_ota(body.get("ota"))
    if not ota:
        raise ValueError(f"ota must be {SUPPORTED_OTA_HINT}")
    hotel_id = str(body.get("hotel_id") or body.get("property_id") or "").strip()
    code = str(body.get("ota_room_type_code") or "").strip()
    room_type_id = str(body.get("room_type_id") or "").strip()
    if not hotel_id or not code or not room_type_id:
        raise ValueError("hotel_id, ota_room_type_code, and room_type_id are required")
    conn = _erp()
    try:
        ensure_schema(conn)
        rt = conn.execute(
            """SELECT id, name, code FROM hospitalityclaw_room_type
               WHERE id = ? AND tenant_id = ? AND hotel_id = ?""",
            (room_type_id, tenant_id, hotel_id),
        ).fetchone()
        if not rt:
            raise ValueError("Room type does not belong to this property")
        existing = conn.execute(
            """SELECT id FROM hospitalityclaw_channel_room_map
               WHERE tenant_id = ? AND hotel_id = ? AND ota = ? AND ota_room_type_code = ?""",
            (tenant_id, hotel_id, ota, code),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE hospitalityclaw_channel_room_map SET room_type_id = ? WHERE id = ?",
                (room_type_id, _g(existing, "id")),
            )
            map_id = _g(existing, "id")
        else:
            map_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO hospitalityclaw_channel_room_map
                   (id, tenant_id, hotel_id, ota, ota_room_type_code, room_type_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (map_id, tenant_id, hotel_id, ota, code, room_type_id),
            )
        conn.commit()
        return _mapping_view(conn, tenant_id, map_id)
    finally:
        conn.close()


def delete_mapping(tenant_id: str, mapping_id: str) -> None:
    conn = _erp()
    try:
        ensure_schema(conn)
        conn.execute(
            "DELETE FROM hospitalityclaw_channel_room_map WHERE id = ? AND tenant_id = ?",
            (mapping_id, tenant_id),
        )
        conn.commit()
    finally:
        conn.close()


def _mapping_view(conn, tenant_id: str, mapping_id: str) -> dict:
    row = conn.execute(
        """SELECT m.*, rt.name AS room_type_name, rt.code AS room_type_code
           FROM hospitalityclaw_channel_room_map m
           JOIN hospitalityclaw_room_type rt ON rt.id = m.room_type_id
           WHERE m.id = ? AND m.tenant_id = ?""",
        (mapping_id, tenant_id),
    ).fetchone()
    if not row:
        return {}
    numbers = conn.execute(
        """SELECT room_number FROM hospitalityclaw_room
           WHERE tenant_id = ? AND hotel_id = ? AND room_type_id = ?
           ORDER BY room_number""",
        (tenant_id, _g(row, "hotel_id"), _g(row, "room_type_id")),
    ).fetchall()
    return {
        "id": _g(row, "id"),
        "hotel_id": _g(row, "hotel_id"),
        "property_id": _g(row, "hotel_id"),
        "ota": _g(row, "ota"),
        "ota_label": source_label(_g(row, "ota") or ""),
        "ota_room_type_code": _g(row, "ota_room_type_code"),
        "room_type_id": _g(row, "room_type_id"),
        "room_type_name": _g(row, "room_type_name"),
        "room_type_code": _g(row, "room_type_code"),
        "room_numbers": [_g(n, "room_number") for n in numbers],
    }


def dashboard(tenant_id: str) -> dict:
    conn = _erp()
    try:
        ensure_schema(conn)
        hotels = conn.execute(
            "SELECT id, name, currency FROM hospitalityclaw_hotel WHERE tenant_id = ? ORDER BY name",
            (tenant_id,),
        ).fetchall()
        connections = conn.execute(
            """SELECT * FROM hospitalityclaw_channel_connection
               WHERE tenant_id = ? ORDER BY ota""",
            (tenant_id,),
        ).fetchall()
        maps = conn.execute(
            "SELECT id FROM hospitalityclaw_channel_room_map WHERE tenant_id = ? ORDER BY ota, ota_room_type_code",
            (tenant_id,),
        ).fetchall()
        logs = conn.execute(
            """SELECT * FROM hospitalityclaw_channel_sync_log
               WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 50""",
            (tenant_id,),
        ).fetchall()
        room_types = conn.execute(
            """SELECT id, hotel_id, name, code, base_rate
               FROM hospitalityclaw_room_type WHERE tenant_id = ? ORDER BY name""",
            (tenant_id,),
        ).fetchall()
        return {
            "webhook_path": WEBHOOK_PATH,
            "otas": [{"code": code, "label": label} for code, label in OTAS.items()],
            "providers": list(PROVIDERS),
            "hotels": [
                {"id": _g(h, "id"), "name": _g(h, "name"), "currency": _g(h, "currency")}
                for h in hotels
            ],
            "connections": [_mask_connection(c) for c in connections],
            "mappings": [_mapping_view(conn, tenant_id, _g(m, "id")) for m in maps],
            "room_types": [
                {
                    "id": _g(rt, "id"),
                    "hotel_id": _g(rt, "hotel_id"),
                    "name": _g(rt, "name"),
                    "code": _g(rt, "code"),
                    "base_rate": _g(rt, "base_rate"),
                }
                for rt in room_types
            ],
            "logs": [_log_view(row) for row in logs],
            "errors": [_log_view(row) for row in logs if _g(row, "status") == "error"],
        }
    finally:
        conn.close()


def _log_view(row) -> dict:
    return {
        "id": _g(row, "id"),
        "hotel_id": _g(row, "hotel_id"),
        "ota": _g(row, "ota"),
        "ota_label": source_label(_g(row, "ota") or "") if _g(row, "ota") else "",
        "direction": _g(row, "direction"),
        "kind": _g(row, "kind"),
        "status": _g(row, "status"),
        "message": _g(row, "message"),
        "created_at": _g(row, "created_at"),
    }


def list_sync_log(
    tenant_id: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    direction: str | None = None,
    status: str | None = None,
    ota: str | None = None,
    kind: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """Paginated channel sync / booking import log for reports."""
    conn = _erp()
    try:
        ensure_schema(conn)
        limit = max(1, min(1000, int(limit or 200)))
        offset = max(0, int(offset or 0))
        where = ["tenant_id = ?"]
        params: list = [tenant_id]
        if from_date:
            where.append("substr(CAST(created_at AS TEXT), 1, 10) >= ?")
            params.append(str(from_date)[:10])
        if to_date:
            where.append("substr(CAST(created_at AS TEXT), 1, 10) <= ?")
            params.append(str(to_date)[:10])
        if direction:
            where.append("direction = ?")
            params.append(direction)
        if status:
            where.append("status = ?")
            params.append(status)
        if ota:
            where.append("ota = ?")
            params.append(ota)
        if kind:
            where.append("kind = ?")
            params.append(kind)
        clause = " AND ".join(where)
        total_row = conn.execute(
            f"SELECT COUNT(*) AS c FROM hospitalityclaw_channel_sync_log WHERE {clause}",
            params,
        ).fetchone()
        total = int(_g(total_row, "c") or 0)
        rows = conn.execute(
            f"""SELECT * FROM hospitalityclaw_channel_sync_log
                WHERE {clause}
                ORDER BY created_at DESC
                LIMIT {limit} OFFSET {offset}""",
            params,
        ).fetchall()
        # Join recent inbound booking imports to reservation ids when payload has them
        booking_map: dict[str, list[str]] = {}
        try:
            bookings = conn.execute(
                """SELECT ota, external_id, reservation_id, reservation_ids
                   FROM hospitalityclaw_channel_booking WHERE tenant_id = ?""",
                (tenant_id,),
            ).fetchall()
            for b in bookings:
                key = f"{_g(b, 'ota')}:{_g(b, 'external_id')}"
                ids = []
                if _g(b, "reservation_id"):
                    ids.append(str(_g(b, "reservation_id")))
                raw_ids = _g(b, "reservation_ids")
                if raw_ids:
                    try:
                        import json as _json
                        parsed = _json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
                        if isinstance(parsed, list):
                            ids.extend(str(x) for x in parsed if x)
                    except Exception:
                        pass
                booking_map[key] = list(dict.fromkeys(ids))
        except Exception:
            booking_map = {}

        logs = []
        for row in rows:
            item = _log_view(row)
            # Best-effort: extract external_id from message/payload for booking rows
            payload = _g(row, "payload_json")
            reservation_ids: list[str] = []
            if payload:
                try:
                    import json as _json
                    data = _json.loads(payload) if isinstance(payload, str) else payload
                    if isinstance(data, dict):
                        if isinstance(data.get("reservation_ids"), list):
                            reservation_ids = [str(x) for x in data["reservation_ids"] if x]
                        elif data.get("reservation_id"):
                            reservation_ids = [str(data["reservation_id"])]
                        ext = data.get("external_id")
                        if ext and not reservation_ids:
                            reservation_ids = booking_map.get(f"{item.get('ota')}:{ext}") or []
                except Exception:
                    pass
            item["reservation_ids"] = reservation_ids
            logs.append(item)

        errors = sum(1 for L in logs if L.get("status") == "error")
        imports = sum(1 for L in logs if L.get("kind") == "booking" and L.get("direction") == "inbound")
        return {
            "logs": logs,
            "total": total,
            "limit": limit,
            "offset": offset,
            "summary": {
                "returned": len(logs),
                "errors": errors,
                "booking_imports": imports,
            },
        }
    finally:
        conn.close()


def list_booking_imports(
    tenant_id: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    ota: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """Audit trail: OTA external booking id → PMS reservations (ops + compliance)."""
    import json as _json

    conn = _erp()
    try:
        ensure_schema(conn)
        limit = max(1, min(1000, int(limit or 200)))
        offset = max(0, int(offset or 0))
        where = ["b.tenant_id = ?"]
        params: list = [tenant_id]
        if from_date:
            where.append("substr(CAST(b.created_at AS TEXT), 1, 10) >= ?")
            params.append(str(from_date)[:10])
        if to_date:
            where.append("substr(CAST(b.created_at AS TEXT), 1, 10) <= ?")
            params.append(str(to_date)[:10])
        if ota:
            where.append("b.ota = ?")
            params.append(ota)
        clause = " AND ".join(where)
        total_row = conn.execute(
            f"SELECT COUNT(*) AS c FROM hospitalityclaw_channel_booking b WHERE {clause}",
            params,
        ).fetchone()
        total = int(_g(total_row, "c") or 0)
        rows = conn.execute(
            f"""SELECT b.*, h.name AS hotel_name
                FROM hospitalityclaw_channel_booking b
                LEFT JOIN hospitalityclaw_hotel h ON h.id = b.hotel_id
                WHERE {clause}
                ORDER BY b.created_at DESC
                LIMIT {limit} OFFSET {offset}""",
            params,
        ).fetchall()

        imports = []
        for row in rows:
            res_ids: list[str] = []
            if _g(row, "reservation_id"):
                res_ids.append(str(_g(row, "reservation_id")))
            raw_ids = _g(row, "reservation_ids")
            if raw_ids:
                try:
                    parsed = _json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
                    if isinstance(parsed, list):
                        res_ids.extend(str(x) for x in parsed if x)
                except Exception:
                    pass
            res_ids = list(dict.fromkeys(res_ids))

            stays = []
            for rid in res_ids:
                stay = conn.execute(
                    """SELECT r.id, r.status, r.check_in, r.check_out, r.rate,
                              g.first_name, g.last_name, g.email,
                              rm.room_number, rt.name AS room_type_name
                       FROM hospitalityclaw_reservation r
                       LEFT JOIN hospitalityclaw_guest g ON g.id = r.guest_id
                       LEFT JOIN hospitalityclaw_room rm ON rm.id = r.room_id
                       LEFT JOIN hospitalityclaw_room_type rt ON rt.id = r.room_type_id
                       WHERE r.tenant_id = ? AND r.id = ?""",
                    (tenant_id, rid),
                ).fetchone()
                if not stay:
                    stays.append({"reservation_id": rid, "missing": True})
                    continue
                guest = f"{_g(stay, 'first_name') or ''} {_g(stay, 'last_name') or ''}".strip()
                stays.append({
                    "reservation_id": rid,
                    "status": _g(stay, "status"),
                    "check_in": _g(stay, "check_in"),
                    "check_out": _g(stay, "check_out"),
                    "rate": _g(stay, "rate"),
                    "guest_name": guest or None,
                    "guest_email": _g(stay, "email"),
                    "room_number": _g(stay, "room_number"),
                    "room_type_name": _g(stay, "room_type_name"),
                })

            imports.append({
                "id": _g(row, "id"),
                "hotel_id": _g(row, "hotel_id"),
                "hotel_name": _g(row, "hotel_name"),
                "ota": _g(row, "ota"),
                "ota_label": source_label(_g(row, "ota") or ""),
                "external_id": _g(row, "external_id"),
                "reservation_id": _g(row, "reservation_id"),
                "reservation_ids": res_ids,
                "stays": stays,
                "created_at": _g(row, "created_at"),
            })

        with_res = sum(1 for i in imports if i["reservation_ids"])
        return {
            "imports": imports,
            "total": total,
            "limit": limit,
            "offset": offset,
            "summary": {
                "returned": len(imports),
                "linked_reservations": with_res,
                "ota_bookings": total,
            },
        }
    finally:
        conn.close()


def channel_sync_report(
    tenant_id: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    direction: str | None = None,
    status: str | None = None,
    ota: str | None = None,
    kind: str | None = None,
    limit: int = 200,
) -> dict:
    """Combined ops sync log + booking import audit for Report 9."""
    ops = list_sync_log(
        tenant_id,
        from_date=from_date,
        to_date=to_date,
        direction=direction,
        status=status,
        ota=ota,
        kind=kind,
        limit=limit,
    )
    audit = list_booking_imports(
        tenant_id,
        from_date=from_date,
        to_date=to_date,
        ota=ota,
        limit=limit,
    )
    return {
        "from_date": from_date,
        "to_date": to_date,
        "ops": ops,
        "audit": audit,
        "summary": {
            "sync_events": ops.get("total") or 0,
            "sync_errors": (ops.get("summary") or {}).get("errors") or 0,
            "booking_imports": audit.get("total") or 0,
            "linked_reservations": (audit.get("summary") or {}).get("linked_reservations") or 0,
        },
    }


def _inventory_rows(conn, tenant_id: str, hotel_id: str, horizon: int) -> list[dict]:
    """Per room type, per night: sellable count and nightly rate."""
    hq, _ = _hq()
    # Owner closed the branch — advertise zero rooms to every OTA.
    try:
        from property_scope import is_accepting_bookings
        bookings_open = is_accepting_bookings(conn, tenant_id)
    except Exception:
        bookings_open = True
    try:
        from services.inventory_controls import ensure_schema as inv_schema
        inv_schema(conn)
    except Exception:
        pass
    room_types = conn.execute(
        """SELECT * FROM hospitalityclaw_room_type
           WHERE tenant_id = ? AND hotel_id = ?""",
        (tenant_id, hotel_id),
    ).fetchall()
    today = date.today()
    rows: list[dict] = []
    for rt in room_types:
        rooms = conn.execute(
            """SELECT * FROM hospitalityclaw_room
               WHERE tenant_id = ? AND hotel_id = ? AND room_type_id = ?""",
            (tenant_id, hotel_id, _g(rt, "id")),
        ).fetchall()
        for offset in range(horizon):
            day = today + timedelta(days=offset)
            start = day.isoformat()
            end = (day + timedelta(days=1)).isoformat()
            if bookings_open:
                physical = 0
                for room in rooms:
                    if hq._room_bookable_for_dates(
                        conn, tenant_id, room, start, end, immediate=(start <= today.isoformat()),
                    ):
                        physical += 1
                unassigned = conn.execute(
                    """SELECT COUNT(*) FROM hospitalityclaw_reservation
                       WHERE tenant_id = ? AND hotel_id = ? AND room_type_id = ?
                         AND room_id IS NULL
                         AND status IN ('reserved', 'pre_checked_in', 'checked_in')
                         AND check_in < ? AND check_out > ?""",
                    (tenant_id, hotel_id, _g(rt, "id"), end, start),
                ).fetchone()[0]
                availability = max(0, physical - int(unassigned or 0))
            else:
                availability = 0
            # Property-wide stop-sell
            try:
                stop = conn.execute(
                    """SELECT stop_sell FROM hospitalityclaw_inventory_control
                       WHERE tenant_id = ? AND hotel_id = ? AND stay_date = ?
                         AND (room_type_id IS NULL OR room_type_id = ?)
                       ORDER BY CASE WHEN room_type_id IS NULL THEN 1 ELSE 0 END
                       LIMIT 1""",
                    (tenant_id, hotel_id, start, _g(rt, "id")),
                ).fetchone()
                if stop and int(_g(stop, "stop_sell") or 0):
                    availability = 0
            except Exception:
                pass
            rate = hq._resolve_nightly_rate(
                conn, tenant_id, hotel_id, _g(rt, "id"), start, _g(rt, "base_rate") or "0",
            )
            rows.append({
                "room_type_id": _g(rt, "id"),
                "room_type_code": _g(rt, "code"),
                "date": start,
                "availability": availability,
                "rate": _money(rate),
            })
    return rows


def _apply_channel_allotment(
    conn, tenant_id: str, hotel_id: str, ota: str, rows: list[dict]
) -> list[dict]:
    """Cap availability by per-channel allotment when configured."""
    out = []
    for row in rows:
        avail = int(row.get("availability") or 0)
        # Channel-scoped stop-sell from restriction calendar
        try:
            cal = conn.execute(
                """SELECT stop_sell FROM hospitalityclaw_restriction_calendar
                   WHERE tenant_id = ? AND hotel_id = ? AND stay_date = ?
                     AND (room_type_id = '' OR room_type_id = ?)
                     AND (ota = '' OR ota = ?)
                   ORDER BY CASE WHEN ota = '' THEN 1 ELSE 0 END,
                            CASE WHEN room_type_id = '' THEN 1 ELSE 0 END
                   LIMIT 1""",
                (tenant_id, hotel_id, row["date"], row["room_type_id"], ota),
            ).fetchone()
            if cal and int(_g(cal, "stop_sell") or 0):
                avail = 0
        except Exception:
            pass
        allot = conn.execute(
            """SELECT allotment FROM hospitalityclaw_channel_allotment
               WHERE tenant_id = ? AND hotel_id = ? AND ota = ?
                 AND room_type_id = ? AND stay_date = ?""",
            (tenant_id, hotel_id, ota, row["room_type_id"], row["date"]),
        ).fetchone()
        if allot is not None:
            try:
                avail = min(avail, max(0, int(_g(allot, "allotment") or 0)))
            except Exception:
                pass
        out.append({**row, "availability": avail})
    return out


def _mapped_rows(conn, tenant_id: str, hotel_id: str, ota: str, rows: list[dict]) -> tuple[list[dict], list[str]]:
    maps = conn.execute(
        """SELECT ota_room_type_code, room_type_id FROM hospitalityclaw_channel_room_map
           WHERE tenant_id = ? AND hotel_id = ? AND ota = ?""",
        (tenant_id, hotel_id, ota),
    ).fetchall()
    by_type = {_g(m, "room_type_id"): _g(m, "ota_room_type_code") for m in maps}
    errors: list[str] = []
    seen_missing: set[str] = set()
    capped = _apply_channel_allotment(conn, tenant_id, hotel_id, ota, rows)
    mapped: list[dict] = []
    for row in capped:
        code = by_type.get(row["room_type_id"])
        if not code:
            if row["room_type_id"] not in seen_missing:
                seen_missing.add(row["room_type_id"])
                errors.append(
                    f"No {source_label(ota)} mapping for room type {row['room_type_code']}"
                )
            continue
        mapped.append({**row, "ota_room_type_code": code})
    return mapped, errors


def upsert_allotments(tenant_id: str, body: dict) -> dict:
    """Bulk set channel allotment for room type × date range."""
    hotel_id = str(body.get("hotel_id") or body.get("property_id") or "").strip()
    ota = normalize_ota(body.get("ota"))
    room_type_id = str(body.get("room_type_id") or "").strip()
    start = str(body.get("start_date") or body.get("from_date") or "").strip()[:10]
    end = str(body.get("end_date") or body.get("to_date") or start).strip()[:10]
    if not hotel_id or not ota or not room_type_id or not start:
        raise ValueError("hotel_id, ota, room_type_id, and start_date are required")
    try:
        allotment = max(0, int(body.get("allotment")))
    except Exception as exc:
        raise ValueError("allotment must be a non-negative integer") from exc
    notes = str(body.get("notes") or "").strip() or None
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    if end_d < start_d:
        raise ValueError("end_date must be on or after start_date")
    if (end_d - start_d).days > 90:
        raise ValueError("Date range cannot exceed 90 nights")

    conn = _erp()
    try:
        ensure_schema(conn)
        hotel = conn.execute(
            "SELECT id FROM hospitalityclaw_hotel WHERE id = ? AND tenant_id = ?",
            (hotel_id, tenant_id),
        ).fetchone()
        if not hotel:
            raise ValueError("Hotel not found")
        rt = conn.execute(
            """SELECT id FROM hospitalityclaw_room_type
               WHERE id = ? AND tenant_id = ? AND hotel_id = ?""",
            (room_type_id, tenant_id, hotel_id),
        ).fetchone()
        if not rt:
            raise ValueError("Room type not found")

        saved = []
        cursor = start_d
        while cursor <= end_d:
            day = cursor.isoformat()
            existing = conn.execute(
                """SELECT id FROM hospitalityclaw_channel_allotment
                   WHERE tenant_id = ? AND hotel_id = ? AND ota = ?
                     AND room_type_id = ? AND stay_date = ?""",
                (tenant_id, hotel_id, ota, room_type_id, day),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE hospitalityclaw_channel_allotment
                       SET allotment = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (allotment, notes, _g(existing, "id")),
                )
                row_id = _g(existing, "id")
            else:
                row_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO hospitalityclaw_channel_allotment
                       (id, tenant_id, hotel_id, ota, room_type_id, stay_date, allotment, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row_id, tenant_id, hotel_id, ota, room_type_id, day, allotment, notes),
                )
            saved.append({
                "id": row_id,
                "hotel_id": hotel_id,
                "ota": ota,
                "ota_label": source_label(ota),
                "room_type_id": room_type_id,
                "stay_date": day,
                "allotment": allotment,
                "notes": notes or "",
            })
            cursor += timedelta(days=1)
        conn.commit()
        try:
            push_outbound(tenant_id, hotel_id=hotel_id, ota=ota, reason="allotment")
        except Exception:
            pass
        return {"allotments": saved, "count": len(saved)}
    finally:
        conn.close()


def list_allotments(
    tenant_id: str,
    hotel_id: str,
    *,
    start_date: str,
    end_date: str,
    ota: str | None = None,
    room_type_id: str | None = None,
) -> list[dict]:
    conn = _erp()
    try:
        ensure_schema(conn)
        sql = """SELECT * FROM hospitalityclaw_channel_allotment
                 WHERE tenant_id = ? AND hotel_id = ?
                   AND stay_date >= ? AND stay_date <= ?"""
        params: list = [tenant_id, hotel_id, start_date[:10], end_date[:10]]
        if ota:
            code = normalize_ota(ota) or ota
            sql += " AND ota = ?"
            params.append(code)
        if room_type_id:
            sql += " AND room_type_id = ?"
            params.append(room_type_id)
        sql += " ORDER BY stay_date, ota, room_type_id"
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": _g(r, "id"),
                "hotel_id": _g(r, "hotel_id"),
                "ota": _g(r, "ota"),
                "ota_label": source_label(_g(r, "ota") or ""),
                "room_type_id": _g(r, "room_type_id"),
                "stay_date": _g(r, "stay_date"),
                "allotment": int(_g(r, "allotment") or 0),
                "notes": _g(r, "notes") or "",
                "updated_at": _g(r, "updated_at"),
            }
            for r in rows
        ]
    finally:
        conn.close()


def _payloads(provider: str, property_external_id: str, rows: list[dict]) -> tuple[dict, dict]:
    if provider == "siteminder":
        availability = {
            "hotelCode": property_external_id,
            "availStatusMessages": [
                {
                    "invTypeCode": row["ota_room_type_code"],
                    "start": row["date"],
                    "end": row["date"],
                    "bookingLimit": row["availability"],
                }
                for row in rows
            ],
        }
        rates = {
            "hotelCode": property_external_id,
            "rateAmountMessages": [
                {
                    "invTypeCode": row["ota_room_type_code"],
                    "start": row["date"],
                    "end": row["date"],
                    "amount": row["rate"],
                }
                for row in rows
            ],
        }
        return availability, rates
    # Channex availability + restrictions. Rate is minor currency units.
    availability = {
        "values": [
            {
                "property_id": property_external_id,
                "room_type_id": row["ota_room_type_code"],
                "date": row["date"],
                "availability": row["availability"],
            }
            for row in rows
        ]
    }
    rates = {
        "values": [
            {
                "property_id": property_external_id,
                "room_type_id": row["ota_room_type_code"],
                "date": row["date"],
                "rate": int(Decimal(row["rate"]) * 100),
                "rate_decimal": row["rate"],
            }
            for row in rows
        ]
    }
    return availability, rates


def _http_post(url: str, payload: dict, headers: dict) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return int(resp.status), resp.read().decode("utf-8", errors="replace")[:2000]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:2000]
        return int(exc.code), body
    except Exception as exc:
        return 0, str(exc)


def _http_post_with_retry(
    url: str,
    payload: dict,
    headers: dict,
    *,
    transport: HttpTransport | None = None,
    attempts: int = 3,
) -> tuple[int, str]:
    """Retry transient outbound failures (timeouts / 5xx) with short backoff."""
    import time

    post = transport or _http_post
    last_status, last_body = 0, "no attempt"
    for attempt in range(1, max(1, attempts) + 1):
        last_status, last_body = post(url, payload, headers)
        if 200 <= last_status < 300:
            return last_status, last_body
        retryable = last_status == 0 or last_status >= 500
        if not retryable or attempt >= attempts:
            break
        time.sleep(min(2 ** (attempt - 1), 4) * 0.25)
    return last_status, last_body


def _auth_headers(provider: str, api_key: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "HotelPilot-ChannelManager/1.0",
    }
    if provider == "siteminder":
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers["user-api-key"] = api_key
    return headers


def push_outbound(
    tenant_id: str,
    *,
    hotel_id: str | None = None,
    ota: str | None = None,
    reason: str = "manual",
    horizon_days: int | None = None,
    transport: HttpTransport | None = None,
) -> dict:
    """Push availability and nightly rates for every connected OTA."""
    try:
        from admin_growth import is_kill_switch_on

        if is_kill_switch_on(tenant_id, "channel_manager_kill"):
            return {
                "status": "ok",
                "pushed": [],
                "errors": [],
                "reason": reason,
                "skipped": "kill_switch",
                "message": "Channel manager disabled by platform kill switch",
            }
    except Exception:
        pass
    horizon = horizon_days or _horizon()
    ota_filter = normalize_ota(ota) if ota else ""
    conn = _erp()
    pushed: list[dict] = []
    errors: list[dict] = []
    try:
        ensure_schema(conn)
        sql = """SELECT * FROM hospitalityclaw_channel_connection
                 WHERE tenant_id = ? AND status != 'disconnected'"""
        params: list[Any] = [tenant_id]
        if hotel_id:
            sql += " AND hotel_id = ?"
            params.append(hotel_id)
        if ota_filter:
            sql += " AND ota = ?"
            params.append(ota_filter)
        connections = conn.execute(sql, params).fetchall()
        if not connections:
            return {"status": "ok", "pushed": [], "errors": [], "reason": reason, "skipped": "no_connections"}

        for link in connections:
            ota_code = _g(link, "ota")
            hid = _g(link, "hotel_id")
            provider = _g(link, "provider") or "channex"
            label = source_label(ota_code)
            external = (_g(link, "property_external_id") or "").strip() or hid
            api_key = (_g(link, "api_key") or "").strip()
            base = _default_base(provider, _g(link, "api_base_url"))
            inventory = _inventory_rows(conn, tenant_id, hid, horizon)
            mapped, map_errors = _mapped_rows(conn, tenant_id, hid, ota_code, inventory)
            for message in map_errors:
                _log(conn, tenant_id, hid, ota_code, "outbound", "mapping", "error", message)
                errors.append({"ota": ota_code, "hotel_id": hid, "message": message})
            if not mapped:
                detail = map_errors[0] if map_errors else f"No room types to push for {label}"
                conn.execute(
                    """UPDATE hospitalityclaw_channel_connection
                       SET status = 'error', last_error = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (detail[:300], _g(link, "id")),
                )
                continue
            if not api_key or not base:
                message = f"{label} API base URL or API key is not configured"
                _log(conn, tenant_id, hid, ota_code, "outbound", "availability", "error", message)
                conn.execute(
                    """UPDATE hospitalityclaw_channel_connection
                       SET status = 'error', last_error = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (message, _g(link, "id")),
                )
                errors.append({"ota": ota_code, "hotel_id": hid, "message": message})
                continue

            availability, rates = _payloads(provider, external, mapped)
            headers = _auth_headers(provider, api_key)
            avail_url = f"{base}/availability"
            rate_url = f"{base}/restrictions" if provider == "channex" else f"{base}/rates"
            a_status, a_body = _http_post_with_retry(
                avail_url, availability, headers, transport=transport,
            )
            r_status, r_body = _http_post_with_retry(
                rate_url, rates, headers, transport=transport,
            )
            ok_http = 200 <= a_status < 300 and 200 <= r_status < 300
            if ok_http:
                stamp = _now()
                conn.execute(
                    """UPDATE hospitalityclaw_channel_connection
                       SET status = 'connected', last_success_at = ?, last_error = NULL,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (stamp, _g(link, "id")),
                )
                _log(
                    conn, tenant_id, hid, ota_code, "outbound", "availability", "success",
                    f"Pushed {len(mapped)} availability/rate rows ({reason})",
                    {"dates": len({row["date"] for row in mapped}), "reason": reason},
                )
                pushed.append({
                    "ota": ota_code,
                    "ota_label": label,
                    "hotel_id": hid,
                    "rows": len(mapped),
                    "availability_status": a_status,
                    "rates_status": r_status,
                    "last_success_at": stamp,
                })
            else:
                message = f"{label} push failed (availability {a_status}, rates {r_status})"
                _log(
                    conn, tenant_id, hid, ota_code, "outbound", "availability", "error", message,
                    {"availability": a_body[:300], "rates": r_body[:300]},
                )
                conn.execute(
                    """UPDATE hospitalityclaw_channel_connection
                       SET status = 'error', last_error = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (message, _g(link, "id")),
                )
                errors.append({"ota": ota_code, "hotel_id": hid, "message": message})
        conn.commit()
        return {
            "status": "ok" if not errors else "partial",
            "pushed": pushed,
            "errors": errors,
            "reason": reason,
        }
    finally:
        conn.close()


def _header(headers: dict, name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value or "")
    return ""


def _signature_ok(secret: str, raw: bytes, header_value: str) -> bool:
    if not secret or not header_value:
        return False
    expected = webhook_signature(secret, raw)
    candidate = header_value.strip()
    if hmac.compare_digest(candidate, expected):
        return True
    digest = expected.split("=", 1)[1]
    return hmac.compare_digest(candidate, digest)


def _parse_booking(body: dict) -> dict:
    """Normalize a flat PMS payload or a Channex-style booking notification."""
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else body
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if data and isinstance(data.get("attributes"), dict):
        attrs = data["attributes"]
        payload = {**attrs, "booking_id": data.get("id") or attrs.get("unique_id")}

    ota = normalize_ota(
        payload.get("ota")
        or payload.get("ota_name")
        or payload.get("channel")
        or body.get("ota")
        or body.get("ota_name")
    )
    external_id = str(
        payload.get("booking_id")
        or payload.get("id")
        or payload.get("unique_id")
        or payload.get("external_id")
        or ""
    ).strip()
    property_id = str(
        payload.get("property_id")
        or payload.get("hotel_id")
        or body.get("property_id")
        or ""
    ).strip()
    status_raw = str(payload.get("status") or body.get("event") or "new").strip().lower()
    if status_raw in ("cancelled", "canceled", "cancel", "booking_cancellation"):
        status = "cancelled"
    else:
        status = "new"

    guest = payload.get("guest") or payload.get("customer") or {}
    if not isinstance(guest, dict):
        guest = {}
    first = str(guest.get("first_name") or guest.get("name") or payload.get("guest_first_name") or "OTA").strip()
    last = str(guest.get("last_name") or guest.get("surname") or payload.get("guest_last_name") or "Guest").strip()
    email = guest.get("email") or guest.get("mail") or payload.get("guest_email")
    phone = guest.get("phone") or payload.get("guest_phone")

    stay_in = str(payload.get("check_in") or payload.get("arrival_date") or "")[:10]
    stay_out = str(payload.get("check_out") or payload.get("departure_date") or "")[:10]
    rooms_in = payload.get("rooms")
    rooms: list[dict] = []
    if isinstance(rooms_in, list) and rooms_in:
        for room in rooms_in:
            if not isinstance(room, dict):
                continue
            rooms.append(_normalize_room(room, stay_in, stay_out, payload))
    else:
        rooms.append(_normalize_room(payload, stay_in, stay_out, payload))

    return {
        "ota": ota,
        "external_id": external_id,
        "property_id": property_id,
        "status": status,
        "guest_first": first or "OTA",
        "guest_last": last or "Guest",
        "guest_email": str(email).strip() if email else None,
        "guest_phone": str(phone).strip() if phone else None,
        "rooms": rooms,
    }


def _normalize_room(room: dict, stay_in: str, stay_out: str, payload: dict) -> dict:
    check_in = str(room.get("check_in") or room.get("checkin_date") or room.get("arrival_date") or stay_in)[:10]
    check_out = str(room.get("check_out") or room.get("checkout_date") or room.get("departure_date") or stay_out)[:10]
    code = str(
        room.get("room_type_code")
        or room.get("ota_room_type_code")
        or room.get("room_type_id")
        or room.get("inv_type_code")
        or payload.get("room_type_code")
        or ""
    ).strip()
    days = room.get("days") if isinstance(room.get("days"), dict) else {}
    nightly = room.get("nightly_rate") or room.get("rate") or payload.get("nightly_rate")
    occupancy = room.get("occupancy") if isinstance(room.get("occupancy"), dict) else {}
    adults = room.get("adults") or occupancy.get("adults") or payload.get("adults") or 1
    children = room.get("children") or occupancy.get("children") or payload.get("children") or 0
    return {
        "ota_room_type_code": code,
        "check_in": check_in,
        "check_out": check_out,
        "nightly_rate": str(nightly) if nightly not in (None, "") else None,
        "days": {str(k)[:10]: str(v) for k, v in days.items()},
        "adults": int(adults or 1),
        "children": int(children or 0),
        "room_number": str(room.get("room_number") or "").strip() or None,
    }


def _resolve_connection(conn, property_id: str, ota: str):
    rows = conn.execute(
        """SELECT * FROM hospitalityclaw_channel_connection
           WHERE status != 'disconnected'
             AND (property_external_id = ? OR hotel_id = ?)""",
        (property_id, property_id),
    ).fetchall()
    if ota:
        matched = [row for row in rows if _g(row, "ota") == ota]
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            return matched[0]
    if len(rows) == 1 and (not ota or _g(rows[0], "ota") == ota):
        return rows[0]
    return None


def _resolve_room_type(conn, tenant_id: str, hotel_id: str, ota: str, ota_code: str):
    mapped = conn.execute(
        """SELECT room_type_id FROM hospitalityclaw_channel_room_map
           WHERE tenant_id = ? AND hotel_id = ? AND ota = ? AND ota_room_type_code = ?""",
        (tenant_id, hotel_id, ota, ota_code),
    ).fetchone()
    if mapped:
        return _g(mapped, "room_type_id"), False
    by_code = conn.execute(
        """SELECT id FROM hospitalityclaw_room_type
           WHERE tenant_id = ? AND hotel_id = ? AND (code = ? OR id = ?)""",
        (tenant_id, hotel_id, ota_code, ota_code),
    ).fetchone()
    if by_code:
        return _g(by_code, "id"), False
    return None, True


def _pick_room(
    conn, tenant_id: str, hotel_id: str, room_type_id: str,
    check_in: str, check_out: str, room_number: str | None,
    exclude_room_ids: set[str] | None = None,
):
    hq, _ = _hq()
    rooms = conn.execute(
        """SELECT * FROM hospitalityclaw_room
           WHERE tenant_id = ? AND hotel_id = ? AND room_type_id = ?
           ORDER BY room_number""",
        (tenant_id, hotel_id, room_type_id),
    ).fetchall()
    taken = exclude_room_ids or set()
    today = date.today().isoformat()
    immediate = check_in <= today
    if room_number:
        chosen = next((room for room in rooms if str(_g(room, "room_number")) == room_number), None)
        if not chosen:
            return None, f"Room {room_number} is not part of this room type at the property"
        if _g(chosen, "id") in taken:
            return None, f"Room {room_number} is already allocated on this booking"
        if not hq._room_bookable_for_dates(conn, tenant_id, chosen, check_in, check_out, immediate=immediate):
            return None, f"Room {room_number} is not available {check_in} → {check_out}"
        return chosen, None
    for room in rooms:
        if _g(room, "id") in taken:
            continue
        if hq._room_bookable_for_dates(conn, tenant_id, room, check_in, check_out, immediate=immediate):
            return room, None
    return None, f"No room is free for {check_in} → {check_out}"


def _nights(check_in: str, check_out: str) -> list[str]:
    start = date.fromisoformat(check_in)
    end = date.fromisoformat(check_out)
    days = []
    cursor = start
    while cursor < end:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def _find_or_create_guest(conn, tenant_id: str, first: str, last: str, email: str | None, phone: str | None) -> str:
    if email:
        existing = conn.execute(
            "SELECT id FROM hospitalityclaw_guest WHERE tenant_id = ? AND email = ?",
            (tenant_id, email),
        ).fetchone()
        if existing:
            return _g(existing, "id")
    guest_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_guest
           (id, tenant_id, first_name, last_name, email, phone)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (guest_id, tenant_id, first, last, email, phone),
    )
    return guest_id


def _import_booking(conn, link, parsed: dict) -> dict:
    hq, _ = _hq()
    tenant_id = _g(link, "tenant_id")
    hotel_id = _g(link, "hotel_id")
    ota = _g(link, "ota")
    label = source_label(ota)
    external_id = parsed["external_id"]

    existing = conn.execute(
        """SELECT * FROM hospitalityclaw_channel_booking
           WHERE tenant_id = ? AND ota = ? AND external_id = ?""",
        (tenant_id, ota, external_id),
    ).fetchone()
    if parsed["status"] == "cancelled":
        return _cancel_imported(conn, link, existing, parsed)

    if existing:
        return _modify_imported(conn, link, existing, parsed)

    planned: list[dict] = []
    taken_rooms: set[str] = set()
    for room in parsed["rooms"]:
        code = room["ota_room_type_code"]
        if not code:
            message = f"{label} booking {external_id} is missing an OTA room type code"
            _log(conn, tenant_id, hotel_id, ota, "inbound", "mapping", "error", message)
            raise ValueError(message)
        room_type_id, failed = _resolve_room_type(conn, tenant_id, hotel_id, ota, code)
        if failed or not room_type_id:
            message = (
                f"Failed channel mapping: {label} room type '{code}' "
                f"is not mapped to a room at property {hotel_id}"
            )
            _log(conn, tenant_id, hotel_id, ota, "inbound", "mapping", "error", message, {"external_id": external_id})
            raise ValueError(message)
        check_in = room["check_in"]
        check_out = room["check_out"]
        if not check_in or not check_out or check_out <= check_in:
            raise ValueError("check-out must be after check-in")
        try:
            from services.revenue import required_min_nights
            from services.inventory_controls import assert_stay_allowed, nightly_sellable
            assert_stay_allowed(
                conn, tenant_id, hotel_id, room_type_id, check_in, check_out, ota=ota,
            )
            required = required_min_nights(conn, tenant_id, hotel_id, room_type_id, check_in, check_out)
            nights = len(_nights(check_in, check_out))
            if nights < int(required or 1):
                raise ValueError(
                    f"Minimum stay is {required} night(s) for these dates "
                    f"(this booking is {nights} night{'s' if nights != 1 else ''})"
                )
            for night in _nights(check_in, check_out):
                if nightly_sellable(conn, tenant_id, hotel_id, room_type_id, night) < 1:
                    raise ValueError(
                        f"No sellable inventory left on {night} for this room type"
                    )
        except ValueError:
            raise
        except Exception:
            pass
        chosen, alloc_error = _pick_room(
            conn, tenant_id, hotel_id, room_type_id, check_in, check_out, room.get("room_number"),
            exclude_room_ids=taken_rooms,
        )
        if alloc_error or not chosen:
            message = alloc_error or "No room available"
            _log(conn, tenant_id, hotel_id, ota, "inbound", "booking", "error", message, {"external_id": external_id})
            raise ValueError(message)
        rt = conn.execute(
            "SELECT base_rate FROM hospitalityclaw_room_type WHERE id = ?",
            (room_type_id,),
        ).fetchone()
        fallback = _money(room.get("nightly_rate") or _g(rt, "base_rate") or "0")
        taken_rooms.add(_g(chosen, "id"))
        planned.append({
            "room": room,
            "room_type_id": room_type_id,
            "chosen": chosen,
            "check_in": check_in,
            "check_out": check_out,
            "fallback": fallback,
        })

    reservation_ids: list[str] = []
    folio_ids: list[str] = []
    room_numbers: list[str] = []
    for item in planned:
        room = item["room"]
        room_type_id = item["room_type_id"]
        chosen = item["chosen"]
        check_in = item["check_in"]
        check_out = item["check_out"]
        fallback = item["fallback"]
        guest_id = _find_or_create_guest(
            conn, tenant_id, parsed["guest_first"], parsed["guest_last"],
            parsed["guest_email"], parsed["guest_phone"],
        )
        reservation_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(24)
        notes = f"{label} #{external_id}"
        conn.execute(
            """INSERT INTO hospitalityclaw_reservation
               (id, tenant_id, hotel_id, guest_id, room_id, room_type_id,
                check_in, check_out, adults, children, status, rate, notes, booking_token)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?)""",
            (
                reservation_id, tenant_id, hotel_id, guest_id, _g(chosen, "id"), room_type_id,
                check_in, check_out, int(room.get("adults") or 1), int(room.get("children") or 0),
                fallback, notes, token,
            ),
        )
        folio_id = hq._open_folio(conn, tenant_id, reservation_id, guest_id)
        for night in _nights(check_in, check_out):
            amount = _money(room["days"].get(night) or room.get("nightly_rate") or fallback)
            hq._post_folio_line(
                conn, tenant_id, folio_id,
                f"Room tariff {night}",
                amount, "room", night,
                channel_source=label,
            )
            hq._apply_tax_rules(
                conn, tenant_id, hotel_id, folio_id, amount, "room", night,
                channel_source=label,
            )
        reservation_ids.append(reservation_id)
        folio_ids.append(folio_id)
        room_numbers.append(str(_g(chosen, "room_number")))

    booking_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_channel_booking
           (id, tenant_id, hotel_id, ota, external_id, reservation_id, reservation_ids)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            booking_id, tenant_id, hotel_id, ota, external_id,
            reservation_ids[0], json.dumps(reservation_ids),
        ),
    )
    _log(
        conn, tenant_id, hotel_id, ota, "inbound", "booking", "success",
        f"Imported {label} {external_id} into room {', '.join(room_numbers)}",
        {"reservation_ids": reservation_ids, "source": label},
    )
    return {
        "status": "ok",
        "duplicate": False,
        "ota": label,
        "source": label,
        "external_id": external_id,
        "reservation_id": reservation_ids[0],
        "reservation_ids": reservation_ids,
        "folio_id": folio_ids[0],
        "folio_ids": folio_ids,
        "room_numbers": room_numbers,
        "property_id": hotel_id,
    }


def _modify_imported(conn, link, existing, parsed: dict) -> dict:
    """Apply an OTA amendment (dates / guest / rate) to an already-imported booking."""
    hq, _ = _hq()
    tenant_id = _g(link, "tenant_id")
    hotel_id = _g(link, "hotel_id")
    ota = _g(link, "ota")
    label = source_label(ota)
    external_id = parsed["external_id"]
    raw_ids = _g(existing, "reservation_ids") or "[]"
    try:
        reservation_ids = json.loads(raw_ids) if raw_ids else []
    except Exception:
        reservation_ids = []
    if _g(existing, "reservation_id") and _g(existing, "reservation_id") not in reservation_ids:
        reservation_ids.append(_g(existing, "reservation_id"))
    if not reservation_ids:
        return {
            "status": "ok",
            "duplicate": True,
            "modified": False,
            "reservation_id": None,
            "external_id": external_id,
            "ota": label,
        }

    rooms = parsed.get("rooms") or []
    updated: list[str] = []
    for idx, reservation_id in enumerate(reservation_ids):
        res = conn.execute(
            """SELECT * FROM hospitalityclaw_reservation
               WHERE id = ? AND tenant_id = ?""",
            (reservation_id, tenant_id),
        ).fetchone()
        if not res:
            continue
        status = _g(res, "status")
        if status in ("cancelled", "checked_out", "checked_in"):
            continue
        room = rooms[idx] if idx < len(rooms) else (rooms[0] if rooms else {})
        check_in = str(room.get("check_in") or _g(res, "check_in"))[:10]
        check_out = str(room.get("check_out") or _g(res, "check_out"))[:10]
        if not check_in or not check_out or check_out <= check_in:
            raise ValueError("check-out must be after check-in")
        adults = int(room.get("adults") or _g(res, "adults") or 1)
        children = int(room.get("children") or _g(res, "children") or 0)
        rate = _money(room.get("nightly_rate") or _g(res, "rate") or "0")
        unchanged = (
            str(_g(res, "check_in") or "")[:10] == check_in
            and str(_g(res, "check_out") or "")[:10] == check_out
            and int(_g(res, "adults") or 1) == adults
            and int(_g(res, "children") or 0) == children
            and _money(_g(res, "rate") or "0") == rate
        )
        if unchanged:
            continue
        guest_id = _find_or_create_guest(
            conn, tenant_id, parsed["guest_first"], parsed["guest_last"],
            parsed["guest_email"], parsed["guest_phone"],
        )
        notes = (_g(res, "notes") or "").strip()
        stamp = f"[Amended by {label}]"
        if stamp not in notes:
            notes = f"{notes}\n{stamp}".strip()
        conn.execute(
            """UPDATE hospitalityclaw_reservation
               SET guest_id = ?, check_in = ?, check_out = ?, adults = ?, children = ?,
                   rate = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND tenant_id = ?""",
            (guest_id, check_in, check_out, adults, children, rate, notes, reservation_id, tenant_id),
        )
        folio = conn.execute(
            """SELECT id FROM hospitalityclaw_folio
               WHERE reservation_id = ? AND tenant_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (reservation_id, tenant_id),
        ).fetchone()
        if folio:
            folio_id = _g(folio, "id")
            conn.execute(
                """DELETE FROM hospitalityclaw_folio_charge
                   WHERE folio_id = ? AND charge_type = 'room'""",
                (folio_id,),
            )
            days = room.get("days") if isinstance(room.get("days"), dict) else {}
            for night in _nights(check_in, check_out):
                amount = _money(days.get(night) or room.get("nightly_rate") or rate)
                hq._post_folio_line(
                    conn, tenant_id, folio_id,
                    f"Room tariff {night}",
                    amount, "room", night,
                    channel_source=label,
                )
                hq._apply_tax_rules(
                    conn, tenant_id, hotel_id, folio_id, amount, "room", night,
                    channel_source=label,
                )
        updated.append(reservation_id)

    if not updated:
        return {
            "status": "ok",
            "duplicate": True,
            "modified": False,
            "reservation_id": reservation_ids[0],
            "external_id": external_id,
            "ota": label,
        }

    _log(
        conn, tenant_id, hotel_id, ota, "inbound", "booking", "success",
        f"Amended {label} booking {external_id}",
        {"reservation_ids": updated},
    )
    return {
        "status": "ok",
        "duplicate": False,
        "modified": True,
        "ota": label,
        "source": label,
        "external_id": external_id,
        "reservation_id": updated[0],
        "reservation_ids": updated,
        "property_id": hotel_id,
    }


def _cancel_imported(conn, link, existing, parsed: dict) -> dict:
    tenant_id = _g(link, "tenant_id")
    hotel_id = _g(link, "hotel_id")
    ota = _g(link, "ota")
    label = source_label(ota)
    external_id = parsed["external_id"]
    if not existing:
        message = f"Cancellation for unknown {label} booking {external_id}"
        _log(conn, tenant_id, hotel_id, ota, "inbound", "booking", "error", message)
        raise ValueError(message)
    raw_ids = _g(existing, "reservation_ids") or "[]"
    try:
        reservation_ids = json.loads(raw_ids) if raw_ids else []
    except Exception:
        reservation_ids = []
    if _g(existing, "reservation_id") and _g(existing, "reservation_id") not in reservation_ids:
        reservation_ids.append(_g(existing, "reservation_id"))
    for reservation_id in reservation_ids:
        res = conn.execute(
            "SELECT status, notes FROM hospitalityclaw_reservation WHERE id = ? AND tenant_id = ?",
            (reservation_id, tenant_id),
        ).fetchone()
        if not res or _g(res, "status") in ("cancelled", "checked_out"):
            continue
        if _g(res, "status") == "checked_in":
            _log(
                conn, tenant_id, hotel_id, ota, "inbound", "booking", "error",
                f"{label} cancelled {external_id} but the guest is already in-house",
            )
            continue
        notes = (_g(res, "notes") or "").strip()
        stamp = f"[Cancelled by {label}]"
        notes = f"{notes}\n{stamp}".strip()
        conn.execute(
            """UPDATE hospitalityclaw_reservation
               SET status = 'cancelled', room_id = NULL, notes = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND tenant_id = ?""",
            (notes, reservation_id, tenant_id),
        )
    _log(
        conn, tenant_id, hotel_id, ota, "inbound", "booking", "success",
        f"Cancelled {label} booking {external_id}",
    )
    return {
        "status": "ok",
        "cancelled": True,
        "external_id": external_id,
        "ota": label,
        "reservation_ids": reservation_ids,
    }


def handle_webhook(raw: bytes, headers: dict | None = None) -> dict:
    """Verify the channel signature, import the stay, and post the room tariff."""
    headers = headers or {}
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return {"status": "error", "message": "Webhook body must be JSON", "http_status": 400}
    if not isinstance(body, dict):
        return {"status": "error", "message": "Webhook body must be a JSON object", "http_status": 400}

    try:
        parsed = _parse_booking(body)
    except Exception as exc:
        return {"status": "error", "message": str(exc), "http_status": 400}
    if not parsed["ota"]:
        return {
            "status": "error",
            "message": f"Unknown OTA. Use {SUPPORTED_OTA_HINT}.",
            "http_status": 400,
        }
    if not parsed["external_id"] or not parsed["property_id"]:
        return {"status": "error", "message": "property_id and booking_id are required", "http_status": 400}

    conn = _erp()
    try:
        ensure_schema(conn)
        link = _resolve_connection(conn, parsed["property_id"], parsed["ota"])
        if not link:
            message = f"No connected channel for property {parsed['property_id']}"
            return {"status": "error", "message": message, "http_status": 404}
        if _g(link, "ota") != parsed["ota"]:
            message = (
                f"OTA {source_label(parsed['ota'])} is not connected for property {parsed['property_id']}"
            )
            _log(conn, _g(link, "tenant_id"), _g(link, "hotel_id"), parsed["ota"], "inbound", "mapping", "error", message)
            conn.commit()
            return {"status": "error", "message": message, "http_status": 422}

        secret = (_g(link, "webhook_secret") or "").strip() or (os.environ.get("OTA_WEBHOOK_SECRET") or "").strip()
        signature = _header(headers, "x-ota-signature") or _header(headers, "x-channel-signature")
        if not _signature_ok(secret, raw, signature):
            return {"status": "error", "message": "Invalid webhook signature", "http_status": 401}

        try:
            from property_scope import is_accepting_bookings
            if parsed["status"] != "cancelled" and not is_accepting_bookings(conn, _g(link, "tenant_id")):
                message = (
                    f"Property {_g(link, 'hotel_id')} is closed for booking — "
                    f"{source_label(parsed['ota'])} reservation rejected"
                )
                _log(
                    conn, _g(link, "tenant_id"), _g(link, "hotel_id"), parsed["ota"],
                    "inbound", "booking", "error", message,
                    {"external_id": parsed["external_id"]},
                )
                conn.commit()
                return {"status": "error", "message": message, "http_status": 409}
        except Exception:
            pass

        try:
            result = _import_booking(conn, link, parsed)
        except ValueError as exc:
            conn.rollback()
            ensure_schema(conn)
            _log(
                conn, _g(link, "tenant_id"), _g(link, "hotel_id"), parsed["ota"],
                "inbound", "mapping" if "mapping" in str(exc).lower() else "booking",
                "error", str(exc), {"external_id": parsed["external_id"]},
            )
            conn.commit()
            return {"status": "error", "message": str(exc), "http_status": 422}
        conn.commit()
        result["http_status"] = 200
        return result
    finally:
        conn.close()


async def sync_after_skill_action(
    *,
    skill: str,
    action: str,
    tenant_id: str | None,
    result: dict | None = None,
    params: dict | None = None,
) -> dict | None:
    """Fire-and-forget from the skill router after a front-desk or rate change."""
    if skill not in ("hospitalityclaw", "hospitality"):
        return None
    if action not in OUTBOUND_ACTIONS or not tenant_id:
        return None
    params = params or {}
    result = result or {}
    hotel_id = params.get("hotel_id") or params.get("hotelId") or result.get("hotel_id")
    import asyncio
    try:
        return await asyncio.to_thread(
            push_outbound,
            str(tenant_id),
            hotel_id=str(hotel_id) if hotel_id else None,
            reason=action,
        )
    except Exception:
        log.exception("Channel outbound sync failed action=%s tenant=%s", action, tenant_id)
        return {"status": "error", "reason": action}
