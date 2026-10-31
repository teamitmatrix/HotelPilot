"""Dynamic pricing / yield management.

Hotel managers define occupancy and length-of-stay rules. A daily job
evaluates them across a booking horizon, upserts high-priority
``YIELD-*`` rate plans (and stay restrictions), then notifies the
channel manager so OTA rates stay in sync.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import db

log = logging.getLogger("webclaw.revenue")

YIELD_CODE_PREFIX = "YLD-"
YIELD_PRIORITY_BASE = 900
DEFAULT_HORIZON_DAYS = 14
RULE_KINDS = ("occupancy", "min_los")
OCCUPANCY_OPS = ("gt", "gte", "lt", "lte")
WEEKDAY_NAMES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

REVENUE_DDL = """
CREATE TABLE IF NOT EXISTS hospitalityclaw_pricing_rule (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    hotel_id        TEXT NOT NULL,
    room_type_id    TEXT,
    name            TEXT NOT NULL,
    rule_kind       TEXT NOT NULL
                    CHECK(rule_kind IN ('occupancy', 'min_los')),
    priority        INTEGER NOT NULL DEFAULT 10,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    occupancy_op    TEXT CHECK(occupancy_op IS NULL OR occupancy_op IN ('gt','gte','lt','lte')),
    occupancy_pct   TEXT,
    within_hours    INTEGER,
    adjustment_pct  TEXT,
    min_nights      INTEGER,
    days_of_week    TEXT,
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_hclaw_pricing_rule_tenant
    ON hospitalityclaw_pricing_rule(tenant_id, hotel_id, is_active, priority);

CREATE TABLE IF NOT EXISTS hospitalityclaw_stay_restriction (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    hotel_id        TEXT NOT NULL,
    room_type_id    TEXT,
    stay_date       TEXT NOT NULL,
    min_nights      INTEGER NOT NULL DEFAULT 1,
    source_rule_id  TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, hotel_id, room_type_id, stay_date)
);
CREATE INDEX IF NOT EXISTS idx_hclaw_stay_rest_lookup
    ON hospitalityclaw_stay_restriction(tenant_id, hotel_id, stay_date);

CREATE TABLE IF NOT EXISTS hospitalityclaw_yield_run (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    hotel_id        TEXT NOT NULL,
    business_date   TEXT NOT NULL,
    status          TEXT NOT NULL,
    summary_json    TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, hotel_id, business_date)
);
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


def _money(value) -> str:
    try:
        return str(Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid money value: {value}") from exc


def _pct(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid percent: {value}") from exc


def _horizon() -> int:
    raw = os.environ.get("CHANNEL_SYNC_HORIZON_DAYS") or os.environ.get(
        "YIELD_HORIZON_DAYS", str(DEFAULT_HORIZON_DAYS)
    )
    try:
        days = int(raw)
    except ValueError:
        days = DEFAULT_HORIZON_DAYS
    return max(1, min(days, 90))


def ensure_schema(conn) -> None:
    _, init_db = _hq()
    init_db(conn)
    for stmt in REVENUE_DDL.split(";"):
        piece = stmt.strip()
        if piece:
            conn.execute(piece)
    conn.commit()


def parse_days_of_week(value) -> list[int]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        items = value
    else:
        text = str(value).strip()
        if text.startswith("["):
            try:
                items = json.loads(text)
            except Exception as exc:
                raise ValueError("days_of_week must be a JSON array") from exc
        else:
            items = [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]
    out: list[int] = []
    for item in items:
        if isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
            day = int(item)
            if day < 0 or day > 6:
                raise ValueError("Weekday numbers must be 0 (Mon) through 6 (Sun)")
            out.append(day)
            continue
        key = str(item).strip().lower()
        if key not in WEEKDAY_NAMES:
            raise ValueError(f"Unknown weekday: {item}")
        out.append(WEEKDAY_NAMES[key])
    return sorted(set(out))


def _rule_view(row, room_type_name: str | None = None) -> dict:
    days_raw = _g(row, "days_of_week")
    try:
        days = json.loads(days_raw) if days_raw else []
    except Exception:
        days = []
    return {
        "id": _g(row, "id"),
        "hotel_id": _g(row, "hotel_id"),
        "room_type_id": _g(row, "room_type_id"),
        "room_type_name": room_type_name,
        "name": _g(row, "name"),
        "rule_kind": _g(row, "rule_kind"),
        "priority": int(_g(row, "priority") or 0),
        "is_active": bool(int(_g(row, "is_active") or 0)),
        "occupancy_op": _g(row, "occupancy_op"),
        "occupancy_pct": _g(row, "occupancy_pct"),
        "within_hours": _g(row, "within_hours"),
        "adjustment_pct": _g(row, "adjustment_pct"),
        "min_nights": _g(row, "min_nights"),
        "days_of_week": days,
        "notes": _g(row, "notes"),
        "created_at": _g(row, "created_at"),
        "updated_at": _g(row, "updated_at"),
        "summary": _rule_summary(row, days),
    }


def _rule_summary(row, days: list) -> str:
    kind = _g(row, "rule_kind")
    if kind == "occupancy":
        op = {
            "gt": ">",
            "gte": "≥",
            "lt": "<",
            "lte": "≤",
        }.get(_g(row, "occupancy_op") or "", _g(row, "occupancy_op") or "?")
        adj = Decimal(str(_g(row, "adjustment_pct") or "0"))
        verb = f"increase by {adj}%" if adj >= 0 else f"discount by {abs(adj)}%"
        within = _g(row, "within_hours")
        window = f" within {within}h of arrival" if within not in (None, "") else ""
        return f"If occupancy {op} {_g(row, 'occupancy_pct')}%{window}, {verb}"
    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_text = ", ".join(labels[int(d)] for d in days) if days else "any day"
    return f"Minimum {_g(row, 'min_nights')} night(s) on {day_text}"


def dashboard(tenant_id: str) -> dict:
    conn = _erp()
    try:
        ensure_schema(conn)
        hotels = conn.execute(
            "SELECT id, name, currency FROM hospitalityclaw_hotel WHERE tenant_id = ? ORDER BY name",
            (tenant_id,),
        ).fetchall()
        room_types = conn.execute(
            """SELECT id, hotel_id, name, code, base_rate
               FROM hospitalityclaw_room_type WHERE tenant_id = ? ORDER BY name""",
            (tenant_id,),
        ).fetchall()
        rules = conn.execute(
            """SELECT r.*, rt.name AS room_type_name
               FROM hospitalityclaw_pricing_rule r
               LEFT JOIN hospitalityclaw_room_type rt ON rt.id = r.room_type_id
               WHERE r.tenant_id = ?
               ORDER BY r.priority DESC, r.name""",
            (tenant_id,),
        ).fetchall()
        runs = conn.execute(
            """SELECT * FROM hospitalityclaw_yield_run
               WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 20""",
            (tenant_id,),
        ).fetchall()
        yield_plans = conn.execute(
            """SELECT id, hotel_id, room_type_id, name, code, start_date, end_date,
                      nightly_rate, priority, is_active, notes
               FROM hospitalityclaw_rate_plan
               WHERE tenant_id = ? AND code LIKE ?
               ORDER BY start_date, code
               LIMIT 100""",
            (tenant_id, f"{YIELD_CODE_PREFIX}%"),
        ).fetchall()
        restrictions = conn.execute(
            """SELECT * FROM hospitalityclaw_stay_restriction
               WHERE tenant_id = ? AND stay_date >= ?
               ORDER BY stay_date LIMIT 60""",
            (tenant_id, date.today().isoformat()),
        ).fetchall()
        return {
            "horizon_days": _horizon(),
            "hotels": [
                {"id": _g(h, "id"), "name": _g(h, "name"), "currency": _g(h, "currency")}
                for h in hotels
            ],
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
            "rules": [_rule_view(r, _g(r, "room_type_name")) for r in rules],
            "recent_runs": [
                {
                    "id": _g(run, "id"),
                    "hotel_id": _g(run, "hotel_id"),
                    "business_date": _g(run, "business_date"),
                    "status": _g(run, "status"),
                    "summary": json.loads(_g(run, "summary_json") or "{}"),
                    "created_at": _g(run, "created_at"),
                }
                for run in runs
            ],
            "yield_rates": [
                {
                    "id": _g(p, "id"),
                    "hotel_id": _g(p, "hotel_id"),
                    "room_type_id": _g(p, "room_type_id"),
                    "name": _g(p, "name"),
                    "code": _g(p, "code"),
                    "start_date": _g(p, "start_date"),
                    "end_date": _g(p, "end_date"),
                    "nightly_rate": _g(p, "nightly_rate"),
                    "priority": _g(p, "priority"),
                    "notes": _g(p, "notes"),
                }
                for p in yield_plans
            ],
            "stay_restrictions": [
                {
                    "id": _g(s, "id"),
                    "hotel_id": _g(s, "hotel_id"),
                    "room_type_id": _g(s, "room_type_id"),
                    "stay_date": _g(s, "stay_date"),
                    "min_nights": _g(s, "min_nights"),
                    "source_rule_id": _g(s, "source_rule_id"),
                }
                for s in restrictions
            ],
        }
    finally:
        conn.close()


def save_rule(tenant_id: str, body: dict) -> dict:
    hotel_id = str(body.get("hotel_id") or "").strip()
    name = str(body.get("name") or "").strip()
    kind = str(body.get("rule_kind") or body.get("kind") or "").strip().lower()
    if not hotel_id or not name:
        raise ValueError("hotel_id and name are required")
    if kind not in RULE_KINDS:
        raise ValueError("rule_kind must be occupancy or min_los")

    room_type_id = str(body.get("room_type_id") or "").strip() or None
    priority = int(body.get("priority") if body.get("priority") is not None else 10)
    is_active = 1 if str(body.get("is_active", True)).lower() not in ("0", "false", "no", "off") else 0
    notes = (body.get("notes") or None) and str(body.get("notes")).strip()

    occupancy_op = occupancy_pct = within_hours = adjustment_pct = None
    min_nights = days_json = None

    if kind == "occupancy":
        occupancy_op = str(body.get("occupancy_op") or "gt").strip().lower()
        if occupancy_op not in OCCUPANCY_OPS:
            raise ValueError("occupancy_op must be gt, gte, lt, or lte")
        occupancy_pct = str(_pct(body.get("occupancy_pct")))
        adj = _pct(body.get("adjustment_pct"))
        adjustment_pct = str(adj)
        if body.get("within_hours") not in (None, ""):
            within_hours = int(body.get("within_hours"))
            if within_hours < 0:
                raise ValueError("within_hours must be ≥ 0")
    else:
        min_nights = int(body.get("min_nights") or 0)
        if min_nights < 1:
            raise ValueError("min_nights must be at least 1")
        days = parse_days_of_week(body.get("days_of_week") or [5, 6])
        if not days:
            raise ValueError("days_of_week is required for min_los rules")
        days_json = json.dumps(days)

    conn = _erp()
    try:
        ensure_schema(conn)
        hotel = conn.execute(
            "SELECT id FROM hospitalityclaw_hotel WHERE id = ? AND tenant_id = ?",
            (hotel_id, tenant_id),
        ).fetchone()
        if not hotel:
            raise ValueError("Hotel not found")
        if room_type_id:
            rt = conn.execute(
                """SELECT id FROM hospitalityclaw_room_type
                   WHERE id = ? AND tenant_id = ? AND hotel_id = ?""",
                (room_type_id, tenant_id, hotel_id),
            ).fetchone()
            if not rt:
                raise ValueError("Room type does not belong to this property")

        rule_id = str(body.get("id") or "").strip()
        if rule_id:
            existing = conn.execute(
                "SELECT id FROM hospitalityclaw_pricing_rule WHERE id = ? AND tenant_id = ?",
                (rule_id, tenant_id),
            ).fetchone()
            if not existing:
                raise ValueError("Pricing rule not found")
            conn.execute(
                """UPDATE hospitalityclaw_pricing_rule
                   SET hotel_id = ?, room_type_id = ?, name = ?, rule_kind = ?, priority = ?,
                       is_active = ?, occupancy_op = ?, occupancy_pct = ?, within_hours = ?,
                       adjustment_pct = ?, min_nights = ?, days_of_week = ?, notes = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND tenant_id = ?""",
                (
                    hotel_id, room_type_id, name, kind, priority, is_active,
                    occupancy_op, occupancy_pct, within_hours, adjustment_pct,
                    min_nights, days_json, notes, rule_id, tenant_id,
                ),
            )
        else:
            rule_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO hospitalityclaw_pricing_rule
                   (id, tenant_id, hotel_id, room_type_id, name, rule_kind, priority, is_active,
                    occupancy_op, occupancy_pct, within_hours, adjustment_pct,
                    min_nights, days_of_week, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule_id, tenant_id, hotel_id, room_type_id, name, kind, priority, is_active,
                    occupancy_op, occupancy_pct, within_hours, adjustment_pct,
                    min_nights, days_json, notes,
                ),
            )
        conn.commit()
        row = conn.execute(
            """SELECT r.*, rt.name AS room_type_name
               FROM hospitalityclaw_pricing_rule r
               LEFT JOIN hospitalityclaw_room_type rt ON rt.id = r.room_type_id
               WHERE r.id = ?""",
            (rule_id,),
        ).fetchone()
        return _rule_view(row, _g(row, "room_type_name"))
    finally:
        conn.close()


def set_rule_active(tenant_id: str, rule_id: str, is_active: bool) -> dict:
    conn = _erp()
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT id FROM hospitalityclaw_pricing_rule WHERE id = ? AND tenant_id = ?",
            (rule_id, tenant_id),
        ).fetchone()
        if not row:
            raise ValueError("Pricing rule not found")
        conn.execute(
            """UPDATE hospitalityclaw_pricing_rule
               SET is_active = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND tenant_id = ?""",
            (1 if is_active else 0, rule_id, tenant_id),
        )
        conn.commit()
        full = conn.execute(
            """SELECT r.*, rt.name AS room_type_name
               FROM hospitalityclaw_pricing_rule r
               LEFT JOIN hospitalityclaw_room_type rt ON rt.id = r.room_type_id
               WHERE r.id = ?""",
            (rule_id,),
        ).fetchone()
        return _rule_view(full, _g(full, "room_type_name"))
    finally:
        conn.close()


def delete_rule(tenant_id: str, rule_id: str) -> None:
    conn = _erp()
    try:
        ensure_schema(conn)
        conn.execute(
            "DELETE FROM hospitalityclaw_pricing_rule WHERE id = ? AND tenant_id = ?",
            (rule_id, tenant_id),
        )
        conn.execute(
            "DELETE FROM hospitalityclaw_stay_restriction WHERE tenant_id = ? AND source_rule_id = ?",
            (tenant_id, rule_id),
        )
        conn.commit()
    finally:
        conn.close()


def _occupancy_for_date(conn, tenant_id: str, hotel_id: str, room_type_id: str | None, day: str) -> dict:
    end = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    room_sql = """SELECT COUNT(*) FROM hospitalityclaw_room
                  WHERE tenant_id = ? AND hotel_id = ? AND status != 'out_of_order'"""
    room_params: list[Any] = [tenant_id, hotel_id]
    sold_sql = """SELECT COUNT(*) FROM hospitalityclaw_reservation
                  WHERE tenant_id = ? AND hotel_id = ?
                    AND status IN ('reserved', 'pre_checked_in', 'checked_in')
                    AND check_in < ? AND check_out > ?"""
    sold_params: list[Any] = [tenant_id, hotel_id, end, day]
    if room_type_id:
        room_sql += " AND room_type_id = ?"
        room_params.append(room_type_id)
        sold_sql += " AND room_type_id = ?"
        sold_params.append(room_type_id)
    total = int(conn.execute(room_sql, room_params).fetchone()[0] or 0)
    sold = int(conn.execute(sold_sql, sold_params).fetchone()[0] or 0)
    sold = min(sold, total) if total else sold
    pct = (
        (Decimal(sold) / Decimal(total) * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if total > 0
        else Decimal("0")
    )
    return {"rooms_total": total, "rooms_sold": sold, "occupancy_pct": pct}


def _op_match(op: str, actual: Decimal, threshold: Decimal) -> bool:
    if op == "gt":
        return actual > threshold
    if op == "gte":
        return actual >= threshold
    if op == "lt":
        return actual < threshold
    if op == "lte":
        return actual <= threshold
    return False


def _hours_until_date(day: str, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    target = datetime.fromisoformat(f"{day}T00:00:00+00:00")
    return (target - now).total_seconds() / 3600.0


def _base_rate(conn, tenant_id: str, hotel_id: str, room_type_id: str, day: str) -> str:
    """Room-type base rate, falling back to non-yield seasonal plans."""
    rt = conn.execute(
        "SELECT base_rate FROM hospitalityclaw_room_type WHERE id = ? AND tenant_id = ?",
        (room_type_id, tenant_id),
    ).fetchone()
    fallback = _g(rt, "base_rate") or "0"
    try:
        row = conn.execute(
            """SELECT nightly_rate FROM hospitalityclaw_rate_plan
               WHERE tenant_id = ? AND hotel_id = ? AND is_active = 1
                 AND start_date <= ? AND end_date >= ?
                 AND (room_type_id IS NULL OR room_type_id = ?)
                 AND code NOT LIKE ?
               ORDER BY CASE WHEN room_type_id IS NOT NULL THEN 0 ELSE 1 END,
                        priority DESC, start_date DESC
               LIMIT 1""",
            (tenant_id, hotel_id, day, day, room_type_id, f"{YIELD_CODE_PREFIX}%"),
        ).fetchone()
    except Exception:
        return _money(fallback)
    if row:
        return _money(_g(row, "nightly_rate"))
    return _money(fallback)


def _yield_code(room_type_code: str, day: str) -> str:
    compact = "".join(ch for ch in (room_type_code or "ALL").upper() if ch.isalnum())[:8] or "ALL"
    return f"{YIELD_CODE_PREFIX}{compact}-{day.replace('-', '')}"


def _upsert_yield_rate(
    conn, tenant_id: str, hotel_id: str, room_type_id: str, room_type_code: str,
    day: str, nightly_rate: str, priority: int, rule_name: str, rule_id: str,
) -> str:
    code = _yield_code(room_type_code, day)
    existing = conn.execute(
        """SELECT id FROM hospitalityclaw_rate_plan
           WHERE tenant_id = ? AND hotel_id = ? AND code = ?""",
        (tenant_id, hotel_id, code),
    ).fetchone()
    notes = f"yield_rule:{rule_id}|{rule_name}"
    name = f"Yield {room_type_code} {day}"
    if existing:
        conn.execute(
            """UPDATE hospitalityclaw_rate_plan
               SET room_type_id = ?, name = ?, start_date = ?, end_date = ?,
                   nightly_rate = ?, priority = ?, is_active = 1, notes = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (room_type_id, name, day, day, nightly_rate, priority, notes, _g(existing, "id")),
        )
        return _g(existing, "id")
    rid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_rate_plan
           (id, tenant_id, hotel_id, room_type_id, name, code, start_date, end_date,
            nightly_rate, priority, is_active, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
        (rid, tenant_id, hotel_id, room_type_id, name, code, day, day, nightly_rate, priority, notes),
    )
    return rid


def _upsert_restriction(
    conn, tenant_id: str, hotel_id: str, room_type_id: str | None,
    day: str, min_nights: int, rule_id: str,
) -> None:
    existing = conn.execute(
        """SELECT id, min_nights FROM hospitalityclaw_stay_restriction
           WHERE tenant_id = ? AND hotel_id = ? AND stay_date = ?
             AND ((room_type_id IS NULL AND ? IS NULL) OR room_type_id = ?)""",
        (tenant_id, hotel_id, day, room_type_id, room_type_id),
    ).fetchone()
    if existing:
        current = int(_g(existing, "min_nights") or 1)
        if min_nights > current:
            conn.execute(
                """UPDATE hospitalityclaw_stay_restriction
                   SET min_nights = ?, source_rule_id = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (min_nights, rule_id, _g(existing, "id")),
            )
        return
    conn.execute(
        """INSERT INTO hospitalityclaw_stay_restriction
           (id, tenant_id, hotel_id, room_type_id, stay_date, min_nights, source_rule_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), tenant_id, hotel_id, room_type_id, day, min_nights, rule_id),
    )


def _clear_horizon_yield(conn, tenant_id: str, hotel_id: str, start: str, end: str) -> None:
    """Deactivate yield plans in the horizon before rewriting today's evaluation."""
    conn.execute(
        """UPDATE hospitalityclaw_rate_plan
           SET is_active = 0, updated_at = CURRENT_TIMESTAMP
           WHERE tenant_id = ? AND hotel_id = ?
             AND code LIKE ?
             AND start_date >= ? AND start_date <= ?""",
        (tenant_id, hotel_id, f"{YIELD_CODE_PREFIX}%", start, end),
    )
    conn.execute(
        """DELETE FROM hospitalityclaw_stay_restriction
           WHERE tenant_id = ? AND hotel_id = ?
             AND stay_date >= ? AND stay_date <= ?""",
        (tenant_id, hotel_id, start, end),
    )


def evaluate_hotel(
    tenant_id: str,
    hotel_id: str,
    *,
    horizon_days: int | None = None,
    push_channels: bool = True,
    now: datetime | None = None,
) -> dict:
    """Evaluate active pricing rules and write yield rates + stay restrictions."""
    horizon = horizon_days or _horizon()
    now = now or datetime.now(timezone.utc)
    today = now.date()
    end_day = today + timedelta(days=horizon - 1)

    conn = _erp()
    rates_written = 0
    restrictions_written = 0
    base_updates = 0
    applied: list[dict] = []
    try:
        ensure_schema(conn)
        hotel = conn.execute(
            "SELECT id, name FROM hospitalityclaw_hotel WHERE id = ? AND tenant_id = ?",
            (hotel_id, tenant_id),
        ).fetchone()
        if not hotel:
            raise ValueError("Hotel not found")

        rules = conn.execute(
            """SELECT * FROM hospitalityclaw_pricing_rule
               WHERE tenant_id = ? AND hotel_id = ? AND is_active = 1
               ORDER BY priority DESC, created_at""",
            (tenant_id, hotel_id),
        ).fetchall()
        room_types = conn.execute(
            """SELECT id, code, base_rate, name FROM hospitalityclaw_room_type
               WHERE tenant_id = ? AND hotel_id = ?""",
            (tenant_id, hotel_id),
        ).fetchall()

        _clear_horizon_yield(conn, tenant_id, hotel_id, today.isoformat(), end_day.isoformat())

        for offset in range(horizon):
            day = (today + timedelta(days=offset)).isoformat()
            weekday = (today + timedelta(days=offset)).weekday()
            hours_out = _hours_until_date(day, now)

            for rt in room_types:
                rt_id = _g(rt, "id")
                rt_code = _g(rt, "code") or "RT"
                occ = _occupancy_for_date(conn, tenant_id, hotel_id, rt_id, day)
                matching = []
                for rule in rules:
                    if _g(rule, "rule_kind") != "occupancy":
                        continue
                    rule_rt = _g(rule, "room_type_id")
                    if rule_rt and rule_rt != rt_id:
                        continue
                    within = _g(rule, "within_hours")
                    if within not in (None, ""):
                        if hours_out > float(within):
                            continue
                    threshold = Decimal(str(_g(rule, "occupancy_pct") or "0"))
                    if not _op_match(_g(rule, "occupancy_op") or "gt", occ["occupancy_pct"], threshold):
                        continue
                    matching.append(rule)
                if matching:
                    winner = matching[0]  # already sorted by priority DESC
                    base = Decimal(_base_rate(conn, tenant_id, hotel_id, rt_id, day))
                    adj = Decimal(str(_g(winner, "adjustment_pct") or "0"))
                    new_rate = (base * (Decimal("1") + adj / Decimal("100"))).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    if new_rate < 0:
                        new_rate = Decimal("0.00")
                    priority = YIELD_PRIORITY_BASE + int(_g(winner, "priority") or 0)
                    plan_id = _upsert_yield_rate(
                        conn, tenant_id, hotel_id, rt_id, rt_code, day,
                        str(new_rate), priority, _g(winner, "name") or "Yield", _g(winner, "id"),
                    )
                    rates_written += 1
                    applied.append({
                        "date": day,
                        "room_type_id": rt_id,
                        "room_type_code": rt_code,
                        "occupancy_pct": str(occ["occupancy_pct"]),
                        "base_rate": str(base),
                        "nightly_rate": str(new_rate),
                        "rule_id": _g(winner, "id"),
                        "rule_name": _g(winner, "name"),
                        "rate_plan_id": plan_id,
                    })
                    if day == today.isoformat():
                        conn.execute(
                            """UPDATE hospitalityclaw_room_type
                               SET base_rate = ?, updated_at = CURRENT_TIMESTAMP
                               WHERE id = ? AND tenant_id = ?""",
                            (str(new_rate), rt_id, tenant_id),
                        )
                        base_updates += 1

            for rule in rules:
                if _g(rule, "rule_kind") != "min_los":
                    continue
                try:
                    days = json.loads(_g(rule, "days_of_week") or "[]")
                except Exception:
                    days = []
                if weekday not in [int(d) for d in days]:
                    continue
                rule_rt = _g(rule, "room_type_id")
                min_nights = int(_g(rule, "min_nights") or 1)
                if rule_rt:
                    _upsert_restriction(
                        conn, tenant_id, hotel_id, rule_rt, day, min_nights, _g(rule, "id"),
                    )
                    restrictions_written += 1
                else:
                    for rt in room_types:
                        _upsert_restriction(
                            conn, tenant_id, hotel_id, _g(rt, "id"), day, min_nights, _g(rule, "id"),
                        )
                        restrictions_written += 1

        summary = {
            "rates_written": rates_written,
            "restrictions_written": restrictions_written,
            "base_rate_updates": base_updates,
            "rules_active": len(rules),
            "horizon_days": horizon,
            "applied_sample": applied[:20],
            "ran_at": _now(),
        }
        run_id = str(uuid.uuid4())
        existing_run = conn.execute(
            """SELECT id FROM hospitalityclaw_yield_run
               WHERE tenant_id = ? AND hotel_id = ? AND business_date = ?""",
            (tenant_id, hotel_id, today.isoformat()),
        ).fetchone()
        if existing_run:
            conn.execute(
                """UPDATE hospitalityclaw_yield_run
                   SET status = 'ok', summary_json = ?, created_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (json.dumps(summary), _g(existing_run, "id")),
            )
            run_id = _g(existing_run, "id")
        else:
            conn.execute(
                """INSERT INTO hospitalityclaw_yield_run
                   (id, tenant_id, hotel_id, business_date, status, summary_json)
                   VALUES (?, ?, ?, ?, 'ok', ?)""",
                (run_id, tenant_id, hotel_id, today.isoformat(), json.dumps(summary)),
            )
        conn.commit()
    finally:
        conn.close()

    channel_result = None
    if push_channels and (rates_written > 0 or restrictions_written > 0):
        try:
            from services import channel_manager as cm
            channel_result = cm.push_outbound(
                tenant_id, hotel_id=hotel_id, reason="yield-pricing", horizon_days=horizon,
            )
        except Exception as exc:
            log.exception("Channel push after yield failed hotel=%s", hotel_id)
            channel_result = {"status": "error", "message": str(exc)}

    return {
        "status": "ok",
        "hotel_id": hotel_id,
        "run_id": run_id,
        "business_date": today.isoformat(),
        **summary,
        "channel_sync": channel_result,
    }


def evaluate_tenant(tenant_id: str, hotel_id: str | None = None, **kwargs) -> dict:
    conn = _erp()
    try:
        ensure_schema(conn)
        if hotel_id:
            hotels = [{"id": hotel_id}]
        else:
            hotels = conn.execute(
                "SELECT id FROM hospitalityclaw_hotel WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchall()
    finally:
        conn.close()
    results = []
    for hotel in hotels:
        results.append(evaluate_hotel(tenant_id, _g(hotel, "id"), **kwargs))
    return {
        "status": "ok",
        "hotels": len(results),
        "results": results,
    }


def list_hotels_with_active_rules() -> list[dict]:
    conn = _erp()
    try:
        ensure_schema(conn)
        rows = conn.execute(
            """SELECT DISTINCT r.tenant_id, r.hotel_id, h.name
               FROM hospitalityclaw_pricing_rule r
               JOIN hospitalityclaw_hotel h ON h.id = r.hotel_id
               WHERE r.is_active = 1"""
        ).fetchall()
        return [
            {"tenant_id": _g(r, "tenant_id"), "hotel_id": _g(r, "hotel_id"), "name": _g(r, "name")}
            for r in rows
        ]
    finally:
        conn.close()


def already_ran_today(tenant_id: str, hotel_id: str, business_date: str | None = None) -> bool:
    day = business_date or date.today().isoformat()
    conn = _erp()
    try:
        ensure_schema(conn)
        row = conn.execute(
            """SELECT id FROM hospitalityclaw_yield_run
               WHERE tenant_id = ? AND hotel_id = ? AND business_date = ? AND status = 'ok'""",
            (tenant_id, hotel_id, day),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


async def run_due_yield_jobs(*, force: bool = False) -> dict:
    """Scheduler entry: evaluate each hotel with active rules once per day."""
    hotels = list_hotels_with_active_rules()
    ran = []
    skipped = []
    for hotel in hotels:
        tid = hotel["tenant_id"]
        hid = hotel["hotel_id"]
        if not force and already_ran_today(tid, hid):
            skipped.append({"hotel_id": hid, "reason": "already_completed"})
            continue
        try:
            result = evaluate_hotel(tid, hid, push_channels=True)
            ran.append(result)
        except Exception as exc:
            log.exception("Yield job failed hotel=%s", hid)
            skipped.append({"hotel_id": hid, "reason": str(exc)})
    return {"status": "ok", "ran": ran, "skipped": skipped}


def required_min_nights(conn, tenant_id: str, hotel_id: str, room_type_id: str, check_in: str, check_out: str) -> int:
    """Highest min-LOS restriction covering any night of the stay."""
    try:
        ensure_schema(conn)
    except Exception:
        pass
    try:
        start = date.fromisoformat(check_in)
        end = date.fromisoformat(check_out)
    except ValueError:
        return 1
    if end <= start:
        return 1
    nights = []
    cursor = start
    while cursor < end:
        nights.append(cursor.isoformat())
        cursor += timedelta(days=1)
    if not nights:
        return 1
    placeholders = ",".join("?" for _ in nights)
    rows = conn.execute(
        f"""SELECT min_nights FROM hospitalityclaw_stay_restriction
            WHERE tenant_id = ? AND hotel_id = ?
              AND stay_date IN ({placeholders})
              AND (room_type_id IS NULL OR room_type_id = ?)""",
        [tenant_id, hotel_id, *nights, room_type_id],
    ).fetchall()
    required = 1
    for row in rows:
        required = max(required, int(_g(row, "min_nights") or 1))
    return required
