"""Availability grid + manual inventory restrictions (stop-sell, CTA/CTD, LOS)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import db

CONTROL_DDL = """
CREATE TABLE IF NOT EXISTS hospitalityclaw_inventory_control (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL,
    hotel_id            TEXT NOT NULL,
    room_type_id        TEXT,
    stay_date           TEXT NOT NULL,
    stop_sell           INTEGER NOT NULL DEFAULT 0,
    closed_to_arrival   INTEGER NOT NULL DEFAULT 0,
    closed_to_departure INTEGER NOT NULL DEFAULT 0,
    min_nights          INTEGER NOT NULL DEFAULT 1,
    max_nights          INTEGER,
    notes               TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, hotel_id, room_type_id, stay_date)
);
CREATE INDEX IF NOT EXISTS idx_hclaw_inv_ctrl_lookup
    ON hospitalityclaw_inventory_control(tenant_id, hotel_id, stay_date);

CREATE TABLE IF NOT EXISTS hospitalityclaw_restriction_calendar (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL,
    hotel_id            TEXT NOT NULL,
    room_type_id        TEXT NOT NULL DEFAULT '',
    rate_plan_id        TEXT NOT NULL DEFAULT '',
    ota                 TEXT NOT NULL DEFAULT '',
    stay_date           TEXT NOT NULL,
    stop_sell           INTEGER NOT NULL DEFAULT 0,
    closed_to_arrival   INTEGER NOT NULL DEFAULT 0,
    closed_to_departure INTEGER NOT NULL DEFAULT 0,
    min_nights          INTEGER NOT NULL DEFAULT 1,
    max_nights          INTEGER,
    notes               TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, hotel_id, room_type_id, rate_plan_id, ota, stay_date)
);
CREATE INDEX IF NOT EXISTS idx_hclaw_restr_cal_lookup
    ON hospitalityclaw_restriction_calendar(tenant_id, hotel_id, stay_date);
"""


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


def _flag(value) -> int:
    if value in (True, 1, "1", "true", "True", "yes", "on"):
        return 1
    return 0


def ensure_schema(conn=None) -> None:
    own = conn is None
    if own:
        conn = _erp()
    try:
        for stmt in CONTROL_DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)
        # Backfill legacy inventory_control rows into the multi-dimension calendar
        # (room-type / all-rates / all-channels) when calendar is empty for that cell.
        try:
            conn.execute(
                """INSERT OR IGNORE INTO hospitalityclaw_restriction_calendar
                   (id, tenant_id, hotel_id, room_type_id, rate_plan_id, ota, stay_date,
                    stop_sell, closed_to_arrival, closed_to_departure, min_nights, max_nights, notes)
                   SELECT id, tenant_id, hotel_id, COALESCE(room_type_id, ''), '', '', stay_date,
                          stop_sell, closed_to_arrival, closed_to_departure, min_nights, max_nights, notes
                   FROM hospitalityclaw_inventory_control"""
            )
        except Exception:
            pass
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def _dim(value) -> str:
    return str(value or "").strip()


def _parse_day(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    return date.fromisoformat(str(value)[:10])


def availability_grid(
    tenant_id: str,
    hotel_id: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    days: int | None = None,
) -> dict:
    """Sellable inventory matrix: room type × night."""
    from services import channel_manager as cm

    start = _parse_day(start_date, date.today())
    if end_date:
        end = _parse_day(end_date, start)
    else:
        horizon = max(1, min(int(days or 14), 90))
        end = start + timedelta(days=horizon - 1)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    span = (end - start).days + 1
    if span > 90:
        raise ValueError("Date range cannot exceed 90 nights")

    conn = _erp()
    try:
        ensure_schema(conn)
        cm.ensure_schema(conn)
        hotel = conn.execute(
            "SELECT id, name FROM hospitalityclaw_hotel WHERE id = ? AND tenant_id = ?",
            (hotel_id, tenant_id),
        ).fetchone()
        if not hotel:
            raise ValueError("Hotel not found")

        # Reuse channel sellable math for an arbitrary window by temporarily
        # walking nights via a local copy of the inventory loop.
        hq, _ = cm._hq()
        try:
            from property_scope import is_accepting_bookings
            bookings_open = is_accepting_bookings(conn, tenant_id)
        except Exception:
            bookings_open = True

        room_types = conn.execute(
            """SELECT * FROM hospitalityclaw_room_type
               WHERE tenant_id = ? AND hotel_id = ?
               ORDER BY name""",
            (tenant_id, hotel_id),
        ).fetchall()

        controls = conn.execute(
            """SELECT * FROM hospitalityclaw_inventory_control
               WHERE tenant_id = ? AND hotel_id = ?
                 AND stay_date >= ? AND stay_date <= ?""",
            (tenant_id, hotel_id, start.isoformat(), end.isoformat()),
        ).fetchall()
        control_map: dict[tuple[str | None, str], dict] = {}
        for row in controls:
            control_map[(_g(row, "room_type_id"), _g(row, "stay_date"))] = {
                "stop_sell": bool(int(_g(row, "stop_sell") or 0)),
                "closed_to_arrival": bool(int(_g(row, "closed_to_arrival") or 0)),
                "closed_to_departure": bool(int(_g(row, "closed_to_departure") or 0)),
                "min_nights": int(_g(row, "min_nights") or 1),
                "max_nights": _g(row, "max_nights"),
            }

        dates = [(start + timedelta(days=i)).isoformat() for i in range(span)]
        types_out = []
        cells = []
        for rt in room_types:
            rt_id = _g(rt, "id")
            rooms = conn.execute(
                """SELECT * FROM hospitalityclaw_room
                   WHERE tenant_id = ? AND hotel_id = ? AND room_type_id = ?""",
                (tenant_id, hotel_id, rt_id),
            ).fetchall()
            types_out.append({
                "id": rt_id,
                "name": _g(rt, "name"),
                "code": _g(rt, "code"),
                "physical_rooms": len(rooms),
            })
            for day_iso in dates:
                day = date.fromisoformat(day_iso)
                night_end = (day + timedelta(days=1)).isoformat()
                if bookings_open:
                    physical = 0
                    for room in rooms:
                        if hq._room_bookable_for_dates(
                            conn, tenant_id, room, day_iso, night_end,
                            immediate=(day_iso <= date.today().isoformat()),
                        ):
                            physical += 1
                    unassigned = conn.execute(
                        """SELECT COUNT(*) FROM hospitalityclaw_reservation
                           WHERE tenant_id = ? AND hotel_id = ? AND room_type_id = ?
                             AND room_id IS NULL
                             AND status IN ('reserved', 'pre_checked_in', 'checked_in')
                             AND check_in < ? AND check_out > ?""",
                        (tenant_id, hotel_id, rt_id, night_end, day_iso),
                    ).fetchone()[0]
                    assigned = conn.execute(
                        """SELECT COUNT(*) FROM hospitalityclaw_reservation
                           WHERE tenant_id = ? AND hotel_id = ? AND room_type_id = ?
                             AND room_id IS NOT NULL
                             AND status IN ('reserved', 'pre_checked_in', 'checked_in')
                             AND check_in < ? AND check_out > ?""",
                        (tenant_id, hotel_id, rt_id, night_end, day_iso),
                    ).fetchone()[0]
                    # Bookable physical already excludes rooms blocked by assigned stays
                    # via _room_bookable_for_dates; subtract only unassigned holds.
                    availability = max(0, physical - int(unassigned or 0))
                else:
                    physical = len(rooms)
                    unassigned = 0
                    assigned = 0
                    availability = 0

                ctrl = control_map.get((rt_id, day_iso)) or control_map.get((None, day_iso)) or {}
                stop = bool(ctrl.get("stop_sell"))
                sellable = 0 if stop else availability
                cells.append({
                    "room_type_id": rt_id,
                    "date": day_iso,
                    "physical": physical,
                    "assigned": int(assigned or 0),
                    "unassigned": int(unassigned or 0),
                    "availability": availability,
                    "sellable": sellable,
                    "stop_sell": stop,
                    "closed_to_arrival": bool(ctrl.get("closed_to_arrival")),
                    "closed_to_departure": bool(ctrl.get("closed_to_departure")),
                    "min_nights": int(ctrl.get("min_nights") or 1),
                    "max_nights": ctrl.get("max_nights"),
                })

        return {
            "hotel_id": hotel_id,
            "hotel_name": _g(hotel, "name"),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "accepting_bookings": bookings_open,
            "room_types": types_out,
            "dates": dates,
            "cells": cells,
        }
    finally:
        conn.close()


def list_controls(
    tenant_id: str,
    hotel_id: str,
    *,
    start_date: str,
    end_date: str,
    room_type_id: str | None = None,
) -> list[dict]:
    conn = _erp()
    try:
        ensure_schema(conn)
        sql = """SELECT * FROM hospitalityclaw_inventory_control
                 WHERE tenant_id = ? AND hotel_id = ?
                   AND stay_date >= ? AND stay_date <= ?"""
        params: list[Any] = [tenant_id, hotel_id, start_date[:10], end_date[:10]]
        if room_type_id:
            sql += " AND (room_type_id = ? OR room_type_id IS NULL)"
            params.append(room_type_id)
        sql += " ORDER BY stay_date, room_type_id"
        rows = conn.execute(sql, params).fetchall()
        return [_control_view(r) for r in rows]
    finally:
        conn.close()


def _control_view(row) -> dict:
    return {
        "id": _g(row, "id"),
        "hotel_id": _g(row, "hotel_id"),
        "room_type_id": _g(row, "room_type_id") or None,
        "rate_plan_id": (_g(row, "rate_plan_id") or None) or None,
        "ota": (_g(row, "ota") or None) or None,
        "stay_date": _g(row, "stay_date"),
        "stop_sell": bool(int(_g(row, "stop_sell") or 0)),
        "closed_to_arrival": bool(int(_g(row, "closed_to_arrival") or 0)),
        "closed_to_departure": bool(int(_g(row, "closed_to_departure") or 0)),
        "min_nights": int(_g(row, "min_nights") or 1),
        "max_nights": (
            int(_g(row, "max_nights"))
            if _g(row, "max_nights") not in (None, "")
            else None
        ),
        "notes": _g(row, "notes") or "",
        "updated_at": _g(row, "updated_at"),
    }


def _calendar_view(row) -> dict:
    rt = _g(row, "room_type_id") or ""
    rp = _g(row, "rate_plan_id") or ""
    ota = _g(row, "ota") or ""
    return {
        "id": _g(row, "id"),
        "hotel_id": _g(row, "hotel_id"),
        "room_type_id": rt or None,
        "rate_plan_id": rp or None,
        "ota": ota or None,
        "stay_date": _g(row, "stay_date"),
        "stop_sell": bool(int(_g(row, "stop_sell") or 0)),
        "closed_to_arrival": bool(int(_g(row, "closed_to_arrival") or 0)),
        "closed_to_departure": bool(int(_g(row, "closed_to_departure") or 0)),
        "min_nights": int(_g(row, "min_nights") or 1),
        "max_nights": (
            int(_g(row, "max_nights"))
            if _g(row, "max_nights") not in (None, "")
            else None
        ),
        "notes": _g(row, "notes") or "",
        "updated_at": _g(row, "updated_at"),
    }


def upsert_controls(tenant_id: str, body: dict) -> dict:
    """Bulk upsert controls for a date range × room type [/ rate / channel]."""
    hotel_id = str(body.get("hotel_id") or "").strip()
    room_type_id = str(body.get("room_type_id") or "").strip() or None
    rate_plan_id = _dim(body.get("rate_plan_id"))
    ota = _dim(body.get("ota"))
    start = str(body.get("start_date") or body.get("from_date") or "").strip()[:10]
    end = str(body.get("end_date") or body.get("to_date") or start).strip()[:10]
    if not hotel_id or not start:
        raise ValueError("hotel_id and start_date are required")
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    if end_d < start_d:
        raise ValueError("end_date must be on or after start_date")
    if (end_d - start_d).days > 90:
        raise ValueError("Date range cannot exceed 90 nights")

    stop_sell = _flag(body.get("stop_sell"))
    cta = _flag(body.get("closed_to_arrival"))
    ctd = _flag(body.get("closed_to_departure"))
    min_nights = max(1, int(body.get("min_nights") or 1))
    max_raw = body.get("max_nights")
    max_nights = None
    if max_raw not in (None, "", 0, "0"):
        max_nights = max(1, int(max_raw))
        if max_nights < min_nights:
            raise ValueError("max_nights must be >= min_nights")
    notes = str(body.get("notes") or "").strip() or None
    rt_key = room_type_id or ""

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
                raise ValueError("Room type not found")

        saved = []
        cursor = start_d
        while cursor <= end_d:
            day = cursor.isoformat()
            # Legacy property-wide table (no rate/channel scope)
            if not rate_plan_id and not ota:
                existing = conn.execute(
                    """SELECT id FROM hospitalityclaw_inventory_control
                       WHERE tenant_id = ? AND hotel_id = ? AND stay_date = ?
                         AND ((room_type_id IS NULL AND ? IS NULL) OR room_type_id = ?)""",
                    (tenant_id, hotel_id, day, room_type_id, room_type_id),
                ).fetchone()
                if existing:
                    conn.execute(
                        """UPDATE hospitalityclaw_inventory_control
                           SET stop_sell = ?, closed_to_arrival = ?, closed_to_departure = ?,
                               min_nights = ?, max_nights = ?, notes = ?,
                               updated_at = CURRENT_TIMESTAMP
                           WHERE id = ?""",
                        (stop_sell, cta, ctd, min_nights, max_nights, notes, _g(existing, "id")),
                    )
                    row_id = _g(existing, "id")
                else:
                    row_id = str(uuid.uuid4())
                    conn.execute(
                        """INSERT INTO hospitalityclaw_inventory_control
                           (id, tenant_id, hotel_id, room_type_id, stay_date,
                            stop_sell, closed_to_arrival, closed_to_departure,
                            min_nights, max_nights, notes)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            row_id, tenant_id, hotel_id, room_type_id, day,
                            stop_sell, cta, ctd, min_nights, max_nights, notes,
                        ),
                    )

            # Full calendar (room type × rate × channel)
            cal = conn.execute(
                """SELECT id FROM hospitalityclaw_restriction_calendar
                   WHERE tenant_id = ? AND hotel_id = ? AND stay_date = ?
                     AND room_type_id = ? AND rate_plan_id = ? AND ota = ?""",
                (tenant_id, hotel_id, day, rt_key, rate_plan_id, ota),
            ).fetchone()
            if cal:
                conn.execute(
                    """UPDATE hospitalityclaw_restriction_calendar
                       SET stop_sell = ?, closed_to_arrival = ?, closed_to_departure = ?,
                           min_nights = ?, max_nights = ?, notes = ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (stop_sell, cta, ctd, min_nights, max_nights, notes, _g(cal, "id")),
                )
                cal_id = _g(cal, "id")
            else:
                cal_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO hospitalityclaw_restriction_calendar
                       (id, tenant_id, hotel_id, room_type_id, rate_plan_id, ota, stay_date,
                        stop_sell, closed_to_arrival, closed_to_departure,
                        min_nights, max_nights, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        cal_id, tenant_id, hotel_id, rt_key, rate_plan_id, ota, day,
                        stop_sell, cta, ctd, min_nights, max_nights, notes,
                    ),
                )
            row = conn.execute(
                "SELECT * FROM hospitalityclaw_restriction_calendar WHERE id = ?",
                (cal_id,),
            ).fetchone()
            saved.append(_calendar_view(row))
            cursor += timedelta(days=1)
        conn.commit()

        # Push updated availability/restrictions to channels (best-effort)
        try:
            from services import channel_manager as cm
            cm.push_outbound(tenant_id, hotel_id=hotel_id, reason="restrictions")
        except Exception:
            pass

        return {"controls": saved, "count": len(saved)}
    finally:
        conn.close()


def clear_controls(tenant_id: str, body: dict) -> dict:
    hotel_id = str(body.get("hotel_id") or "").strip()
    room_type_id = str(body.get("room_type_id") or "").strip() or None
    start = str(body.get("start_date") or "").strip()[:10]
    end = str(body.get("end_date") or start).strip()[:10]
    if not hotel_id or not start:
        raise ValueError("hotel_id and start_date are required")
    conn = _erp()
    try:
        ensure_schema(conn)
        if room_type_id:
            cur = conn.execute(
                """DELETE FROM hospitalityclaw_inventory_control
                   WHERE tenant_id = ? AND hotel_id = ?
                     AND stay_date >= ? AND stay_date <= ?
                     AND room_type_id = ?""",
                (tenant_id, hotel_id, start, end, room_type_id),
            )
        else:
            cur = conn.execute(
                """DELETE FROM hospitalityclaw_inventory_control
                   WHERE tenant_id = ? AND hotel_id = ?
                     AND stay_date >= ? AND stay_date <= ?""",
                (tenant_id, hotel_id, start, end),
            )
        deleted = cur.rowcount if cur is not None else 0
        conn.commit()
        return {"deleted": int(deleted or 0)}
    finally:
        conn.close()


def _effective_calendar_rows(
    conn,
    tenant_id: str,
    hotel_id: str,
    room_type_id: str,
    stay_nights: list[str],
    *,
    ota: str | None = None,
    rate_plan_id: str | None = None,
) -> list:
    """Merge legacy controls + calendar rows applicable to this stay/channel."""
    if not stay_nights:
        return []
    placeholders = ",".join("?" for _ in stay_nights)
    legacy = conn.execute(
        f"""SELECT stay_date, stop_sell, closed_to_arrival, closed_to_departure,
                   min_nights, max_nights, room_type_id, '' AS rate_plan_id, '' AS ota
            FROM hospitalityclaw_inventory_control
            WHERE tenant_id = ? AND hotel_id = ?
              AND stay_date IN ({placeholders})
              AND (room_type_id IS NULL OR room_type_id = ?)""",
        [tenant_id, hotel_id, *stay_nights, room_type_id],
    ).fetchall()
    ota_key = _dim(ota)
    rate_key = _dim(rate_plan_id)
    calendar = conn.execute(
        f"""SELECT stay_date, stop_sell, closed_to_arrival, closed_to_departure,
                   min_nights, max_nights, room_type_id, rate_plan_id, ota
            FROM hospitalityclaw_restriction_calendar
            WHERE tenant_id = ? AND hotel_id = ?
              AND stay_date IN ({placeholders})
              AND (room_type_id = '' OR room_type_id = ?)
              AND (rate_plan_id = '' OR rate_plan_id = ?)
              AND (ota = '' OR ota = ?)""",
        [tenant_id, hotel_id, *stay_nights, room_type_id or "", rate_key, ota_key],
    ).fetchall()
    return list(legacy) + list(calendar)


def assert_stay_allowed(
    conn,
    tenant_id: str,
    hotel_id: str,
    room_type_id: str,
    check_in: str,
    check_out: str,
    *,
    ota: str | None = None,
    rate_plan_id: str | None = None,
) -> None:
    """Raise ValueError when stop-sell / CTA / CTD / max-LOS blocks the stay."""
    try:
        ensure_schema(conn)
    except Exception:
        pass
    try:
        start = date.fromisoformat(check_in[:10])
        end = date.fromisoformat(check_out[:10])
    except ValueError as exc:
        raise ValueError("Invalid stay dates") from exc
    if end <= start:
        raise ValueError("check-out must be after check-in")
    nights = (end - start).days
    stay_nights = []
    cursor = start
    while cursor < end:
        stay_nights.append(cursor.isoformat())
        cursor += timedelta(days=1)
    if not stay_nights:
        return

    rows = _effective_calendar_rows(
        conn, tenant_id, hotel_id, room_type_id, stay_nights,
        ota=ota, rate_plan_id=rate_plan_id,
    )
    departure_day = end.isoformat()
    dep_rows = _effective_calendar_rows(
        conn, tenant_id, hotel_id, room_type_id, [departure_day],
        ota=ota, rate_plan_id=rate_plan_id,
    )

    arrival_ctrl = None
    min_required = 1
    max_allowed = None
    for row in rows:
        if int(_g(row, "stop_sell") or 0):
            raise ValueError(
                f"Stop-sell is active on {_g(row, 'stay_date')} for this room type"
            )
        if _g(row, "stay_date") == check_in[:10]:
            arrival_ctrl = row
        min_required = max(min_required, int(_g(row, "min_nights") or 1))
        mx = _g(row, "max_nights")
        if mx not in (None, ""):
            max_allowed = int(mx) if max_allowed is None else min(max_allowed, int(mx))

    if arrival_ctrl and int(_g(arrival_ctrl, "closed_to_arrival") or 0):
        raise ValueError(f"Closed to arrival on {check_in[:10]}")

    for row in dep_rows:
        if int(_g(row, "closed_to_departure") or 0):
            raise ValueError(f"Closed to departure on {check_out[:10]}")

    if nights < min_required:
        raise ValueError(
            f"Minimum stay is {min_required} night(s) for these dates "
            f"(this booking is {nights} night{'s' if nights != 1 else ''})"
        )
    if max_allowed is not None and nights > max_allowed:
        raise ValueError(
            f"Maximum stay is {max_allowed} night(s) for these dates "
            f"(this booking is {nights} nights)"
        )


def restriction_calendar(
    tenant_id: str,
    hotel_id: str,
    *,
    start_date: str,
    end_date: str,
    room_type_id: str | None = None,
    rate_plan_id: str | None = None,
    ota: str | None = None,
) -> dict:
    """Matrix of restrictions across room types × nights for calendar UI."""
    start = date.fromisoformat(start_date[:10])
    end = date.fromisoformat(end_date[:10])
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    if (end - start).days > 90:
        raise ValueError("Date range cannot exceed 90 nights")

    conn = _erp()
    try:
        ensure_schema(conn)
        hotel = conn.execute(
            "SELECT id, name FROM hospitalityclaw_hotel WHERE id = ? AND tenant_id = ?",
            (hotel_id, tenant_id),
        ).fetchone()
        if not hotel:
            raise ValueError("Hotel not found")

        rt_sql = """SELECT id, name, code FROM hospitalityclaw_room_type
                    WHERE tenant_id = ? AND hotel_id = ?"""
        rt_params: list[Any] = [tenant_id, hotel_id]
        if room_type_id:
            rt_sql += " AND id = ?"
            rt_params.append(room_type_id)
        rt_sql += " ORDER BY name"
        room_types = conn.execute(rt_sql, rt_params).fetchall()

        rate_plans = []
        try:
            rate_plans = conn.execute(
                """SELECT id, name, code FROM hospitalityclaw_rate_plan
                   WHERE tenant_id = ? AND hotel_id = ? ORDER BY name""",
                (tenant_id, hotel_id),
            ).fetchall()
        except Exception:
            rate_plans = []

        dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        ota_key = _dim(ota)
        rate_key = _dim(rate_plan_id)

        cal_rows = conn.execute(
            """SELECT * FROM hospitalityclaw_restriction_calendar
               WHERE tenant_id = ? AND hotel_id = ?
                 AND stay_date >= ? AND stay_date <= ?
                 AND (rate_plan_id = '' OR rate_plan_id = ?)
                 AND (ota = '' OR ota = ?)""",
            (tenant_id, hotel_id, dates[0], dates[-1], rate_key, ota_key),
        ).fetchall()
        legacy = conn.execute(
            """SELECT * FROM hospitalityclaw_inventory_control
               WHERE tenant_id = ? AND hotel_id = ?
                 AND stay_date >= ? AND stay_date <= ?""",
            (tenant_id, hotel_id, dates[0], dates[-1]),
        ).fetchall()

        # Most specific wins: exact ota+rate > ota > rate > global
        def specificity(row) -> int:
            score = 0
            if _g(row, "room_type_id"):
                score += 1
            if _g(row, "rate_plan_id"):
                score += 2
            if _g(row, "ota"):
                score += 4
            return score

        cell_map: dict[tuple[str, str], dict] = {}
        for row in list(legacy) + list(cal_rows):
            rt = _g(row, "room_type_id") or ""
            day = _g(row, "stay_date")
            if not day:
                continue
            # Apply hotel-wide (empty rt) to all types later
            key = (rt, day)
            prev = cell_map.get(key)
            if prev is None or specificity(row) >= specificity(prev.get("_src") or {}):
                view = _calendar_view(row) if "rate_plan_id" in (row.keys() if hasattr(row, "keys") else []) or _g(row, "ota") is not None else _control_view(row)
                # Normalize from either table
                view = {
                    "room_type_id": rt or None,
                    "rate_plan_id": (_g(row, "rate_plan_id") or None) or None,
                    "ota": (_g(row, "ota") or None) or None,
                    "stay_date": day,
                    "stop_sell": bool(int(_g(row, "stop_sell") or 0)),
                    "closed_to_arrival": bool(int(_g(row, "closed_to_arrival") or 0)),
                    "closed_to_departure": bool(int(_g(row, "closed_to_departure") or 0)),
                    "min_nights": int(_g(row, "min_nights") or 1),
                    "max_nights": (
                        int(_g(row, "max_nights"))
                        if _g(row, "max_nights") not in (None, "")
                        else None
                    ),
                    "_src": row,
                }
                cell_map[key] = view

        cells = []
        for rt in room_types:
            rt_id = _g(rt, "id")
            for day in dates:
                cell = cell_map.get((rt_id, day)) or cell_map.get(("", day)) or {
                    "room_type_id": rt_id,
                    "rate_plan_id": rate_key or None,
                    "ota": ota_key or None,
                    "stay_date": day,
                    "stop_sell": False,
                    "closed_to_arrival": False,
                    "closed_to_departure": False,
                    "min_nights": 1,
                    "max_nights": None,
                }
                out = {k: v for k, v in cell.items() if k != "_src"}
                out["room_type_id"] = rt_id
                cells.append(out)

        return {
            "hotel_id": hotel_id,
            "hotel_name": _g(hotel, "name"),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "filters": {
                "room_type_id": room_type_id,
                "rate_plan_id": rate_key or None,
                "ota": ota_key or None,
            },
            "room_types": [
                {"id": _g(r, "id"), "name": _g(r, "name"), "code": _g(r, "code")}
                for r in room_types
            ],
            "rate_plans": [
                {"id": _g(r, "id"), "name": _g(r, "name"), "code": _g(r, "code")}
                for r in rate_plans
            ],
            "dates": dates,
            "cells": cells,
        }
    finally:
        conn.close()


def nightly_sellable(
    conn,
    tenant_id: str,
    hotel_id: str,
    room_type_id: str,
    night: str,
    *,
    exclude_reservation_id: str | None = None,
) -> int:
    """Physical bookable rooms minus overlapping unassigned holds for one night."""
    from services import channel_manager as cm

    hq, _ = cm._hq()
    try:
        from property_scope import is_accepting_bookings
        if not is_accepting_bookings(conn, tenant_id):
            return 0
    except Exception:
        pass

    ctrl = conn.execute(
        """SELECT stop_sell FROM hospitalityclaw_inventory_control
           WHERE tenant_id = ? AND hotel_id = ? AND stay_date = ?
             AND (room_type_id IS NULL OR room_type_id = ?)
           ORDER BY CASE WHEN room_type_id IS NULL THEN 1 ELSE 0 END
           LIMIT 1""",
        (tenant_id, hotel_id, night[:10], room_type_id),
    ).fetchone()
    if ctrl and int(_g(ctrl, "stop_sell") or 0):
        return 0

    day = date.fromisoformat(night[:10])
    night_end = (day + timedelta(days=1)).isoformat()
    rooms = conn.execute(
        """SELECT * FROM hospitalityclaw_room
           WHERE tenant_id = ? AND hotel_id = ? AND room_type_id = ?""",
        (tenant_id, hotel_id, room_type_id),
    ).fetchall()
    physical = 0
    for room in rooms:
        if hq._room_bookable_for_dates(
            conn, tenant_id, room, night[:10], night_end,
            immediate=(night[:10] <= date.today().isoformat()),
            exclude_reservation_id=exclude_reservation_id,
        ):
            physical += 1
    sql = """SELECT COUNT(*) FROM hospitalityclaw_reservation
             WHERE tenant_id = ? AND hotel_id = ? AND room_type_id = ?
               AND room_id IS NULL
               AND status IN ('reserved', 'pre_checked_in', 'checked_in')
               AND check_in < ? AND check_out > ?"""
    params: list[Any] = [tenant_id, hotel_id, room_type_id, night_end, night[:10]]
    if exclude_reservation_id:
        sql += " AND id != ?"
        params.append(exclude_reservation_id)
    unassigned = conn.execute(sql, params).fetchone()[0]
    return max(0, physical - int(unassigned or 0))
