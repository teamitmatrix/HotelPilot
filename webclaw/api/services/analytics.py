"""Hospitality BI metrics — RevPAR, ADR, occupancy, ALOS, cancellations."""
from __future__ import annotations

import csv
import io
import json
import logging
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import db

log = logging.getLogger("webclaw.analytics")

OTA_LABELS = {
    "booking_com": "Booking.com",
    "airbnb": "Airbnb",
    "expedia": "Expedia",
    "agoda": "Agoda",
}


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


def _money(value) -> str:
    try:
        return str(Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except Exception:
        return "0.00"


def _pct(value) -> str:
    try:
        return str(Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except Exception:
        return "0.00"


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    return date.fromisoformat(str(value)[:10])


def _daterange(start: date, end: date) -> list[date]:
    """Half-open [start, end)."""
    days: list[date] = []
    cursor = start
    while cursor < end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    last = monthrange(day.year, day.month)[1]
    end = date(day.year, day.month, last) + timedelta(days=1)
    return start, end


def resolve_range(
    from_date: str | None = None,
    to_date: str | None = None,
    *,
    today: date | None = None,
) -> tuple[date, date]:
    today = today or date.today()
    if from_date or to_date:
        start = _parse_date(from_date, today.replace(day=1))
        end = _parse_date(to_date, today + timedelta(days=1))
        if end <= start:
            end = start + timedelta(days=1)
        return start, end
    start, end = _month_bounds(today)
    # Cap current-month end at tomorrow so “to date” is exclusive of tomorrow
    # and includes today.
    tomorrow = today + timedelta(days=1)
    if end > tomorrow:
        end = tomorrow
    return start, end


def _hotel_meta(conn, tenant_id: str) -> dict:
    row = conn.execute(
        """SELECT id, name, currency, timezone
           FROM hospitalityclaw_hotel WHERE tenant_id = ? ORDER BY name LIMIT 1""",
        (tenant_id,),
    ).fetchone()
    if not row:
        return {
            "hotel_id": None,
            "hotel_name": "Hotel",
            "currency": "USD",
            "timezone": "UTC",
        }
    return {
        "hotel_id": _g(row, "id"),
        "hotel_name": _g(row, "name") or "Hotel",
        "currency": _g(row, "currency") or "USD",
        "timezone": _g(row, "timezone") or "UTC",
    }


def _available_rooms(conn, tenant_id: str) -> int:
    row = conn.execute(
        """SELECT COUNT(*) AS c FROM hospitalityclaw_room
           WHERE tenant_id = ? AND status != 'out_of_order'""",
        (tenant_id,),
    ).fetchone()
    return int(_g(row, "c") or 0)


def _sold_on_night(conn, tenant_id: str, night: date) -> int:
    day = night.isoformat()
    end = (night + timedelta(days=1)).isoformat()
    row = conn.execute(
        """SELECT COUNT(*) AS c FROM hospitalityclaw_reservation
           WHERE tenant_id = ?
             AND status IN ('reserved', 'pre_checked_in', 'checked_in', 'checked_out')
             AND check_in < ? AND check_out > ?""",
        (tenant_id, end, day),
    ).fetchone()
    return int(_g(row, "c") or 0)


def _room_revenue_on(conn, tenant_id: str, night: date) -> Decimal:
    row = conn.execute(
        """SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) AS total
           FROM hospitalityclaw_folio_charge
           WHERE tenant_id = ? AND charge_type = 'room' AND posting_date = ?""",
        (tenant_id, night.isoformat()),
    ).fetchone()
    return _dec(_g(row, "total"))


def _room_revenue_range(conn, tenant_id: str, start: date, end: date) -> Decimal:
    row = conn.execute(
        """SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) AS total
           FROM hospitalityclaw_folio_charge
           WHERE tenant_id = ? AND charge_type = 'room'
             AND posting_date >= ? AND posting_date < ?""",
        (tenant_id, start.isoformat(), end.isoformat()),
    ).fetchone()
    return _dec(_g(row, "total"))


def _period_kpis(conn, tenant_id: str, start: date, end: date) -> dict:
    days = _daterange(start, end)
    available_per_night = _available_rooms(conn, tenant_id)
    available_nights = available_per_night * len(days) if days else 0
    sold_nights = 0
    daily = []
    for night in days:
        sold = _sold_on_night(conn, tenant_id, night)
        sold_nights += sold
        rev = _room_revenue_on(conn, tenant_id, night)
        daily.append({
            "date": night.isoformat(),
            "room_revenue": _money(rev),
            "rooms_sold": sold,
            "rooms_available": available_per_night,
            "occupancy_pct": _pct(
                (Decimal(sold) / Decimal(available_per_night) * 100) if available_per_night else 0
            ),
        })

    room_revenue = _room_revenue_range(conn, tenant_id, start, end)
    occupancy = (
        (Decimal(sold_nights) / Decimal(available_nights) * 100)
        if available_nights > 0
        else Decimal("0")
    )
    adr = (room_revenue / Decimal(sold_nights)) if sold_nights > 0 else Decimal("0")
    revpar = (room_revenue / Decimal(available_nights)) if available_nights > 0 else Decimal("0")

    # ALOS: stays that overlap the window (non-cancelled)
    stays = conn.execute(
        """SELECT check_in, check_out FROM hospitalityclaw_reservation
           WHERE tenant_id = ?
             AND status NOT IN ('cancelled')
             AND check_in < ? AND check_out > ?""",
        (tenant_id, end.isoformat(), start.isoformat()),
    ).fetchall()
    lengths: list[int] = []
    for stay in stays:
        try:
            cin = date.fromisoformat(str(_g(stay, "check_in"))[:10])
            cout = date.fromisoformat(str(_g(stay, "check_out"))[:10])
            nights = (cout - cin).days
            if nights > 0:
                lengths.append(nights)
        except Exception:
            continue
    alos = (
        (Decimal(sum(lengths)) / Decimal(len(lengths))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if lengths
        else Decimal("0")
    )

    # Cancellation rate: reservations created in window
    created = conn.execute(
        """SELECT
              SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
              COUNT(*) AS total
           FROM hospitalityclaw_reservation
           WHERE tenant_id = ?
             AND date(created_at) >= date(?)
             AND date(created_at) < date(?)""",
        (tenant_id, start.isoformat(), end.isoformat()),
    ).fetchone()
    cancelled = int(_g(created, "cancelled") or 0)
    total_created = int(_g(created, "total") or 0)
    cancel_rate = (
        (Decimal(cancelled) / Decimal(total_created) * 100)
        if total_created > 0
        else Decimal("0")
    )

    return {
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "days": len(days),
        "room_revenue": _money(room_revenue),
        "rooms_available": available_per_night,
        "available_room_nights": available_nights,
        "rooms_sold": sold_nights,
        "occupancy_pct": _pct(occupancy),
        "adr": _money(adr),
        "revpar": _money(revpar),
        "alos": str(alos),
        "cancellation_rate_pct": _pct(cancel_rate),
        "cancellations": cancelled,
        "reservations_created": total_created,
        "daily": daily,
    }


def expected_occupancy(
    tenant_id: str,
    *,
    days: int = 14,
    from_date: str | None = None,
) -> dict:
    """Forward-looking occupancy: reserved/in-house rooms per night for the next N days."""
    days = max(1, min(90, int(days or 14)))
    conn = _erp()
    try:
        try:
            from hospitalityclaw.init_db import init_db
            init_db(conn)
        except Exception:
            pass
        start = date.fromisoformat(from_date) if from_date else date.today()
        end = start + timedelta(days=days)
        meta = _hotel_meta(conn, tenant_id)
        available = _available_rooms(conn, tenant_id)
        daily = []
        sold_total = 0
        for night in _daterange(start, end):
            sold = _sold_on_night(conn, tenant_id, night)
            sold_total += sold
            arrivals = conn.execute(
                """SELECT COUNT(*) AS c FROM hospitalityclaw_reservation
                   WHERE tenant_id = ?
                     AND status IN ('reserved', 'pre_checked_in', 'checked_in')
                     AND check_in = ?""",
                (tenant_id, night.isoformat()),
            ).fetchone()
            departures = conn.execute(
                """SELECT COUNT(*) AS c FROM hospitalityclaw_reservation
                   WHERE tenant_id = ?
                     AND status IN ('reserved', 'pre_checked_in', 'checked_in', 'checked_out')
                     AND check_out = ?""",
                (tenant_id, night.isoformat()),
            ).fetchone()
            occ = (
                (Decimal(sold) / Decimal(available) * 100) if available else Decimal("0")
            )
            daily.append({
                "date": night.isoformat(),
                "rooms_sold": sold,
                "rooms_available": available,
                "rooms_vacant": max(0, available - sold),
                "occupancy_pct": _pct(occ),
                "arrivals": int(_g(arrivals, "c") or 0),
                "departures": int(_g(departures, "c") or 0),
            })
        available_nights = available * days
        avg_occ = (
            (Decimal(sold_total) / Decimal(available_nights) * 100)
            if available_nights
            else Decimal("0")
        )
        peak = max((row["occupancy_pct"] for row in daily), default="0.00")
        return {
            "status": "ok",
            "hotel": meta,
            "period": {
                "from_date": start.isoformat(),
                "to_date": (end - timedelta(days=1)).isoformat(),
                "days": days,
            },
            "summary": {
                "rooms_available": available,
                "room_nights_sold": sold_total,
                "available_room_nights": available_nights,
                "avg_occupancy_pct": _pct(avg_occ),
                "peak_occupancy_pct": peak,
            },
            "daily": daily,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        conn.close()


def _channel_reservation_ids(conn, tenant_id: str) -> dict[str, str]:
    """reservation_id → ota code."""
    mapping: dict[str, str] = {}
    try:
        rows = conn.execute(
            """SELECT ota, reservation_id, reservation_ids
               FROM hospitalityclaw_channel_booking WHERE tenant_id = ?""",
            (tenant_id,),
        ).fetchall()
    except Exception:
        return mapping
    for row in rows:
        ota = _g(row, "ota") or "ota"
        rid = _g(row, "reservation_id")
        if rid:
            mapping[str(rid)] = ota
        raw = _g(row, "reservation_ids") or "[]"
        try:
            ids = json.loads(raw) if raw else []
        except Exception:
            ids = []
        for item in ids:
            if item:
                mapping[str(item)] = ota
    return mapping


def _classify_source(notes: str | None, channel_ota: str | None) -> tuple[str, str]:
    """Return (bucket, label). bucket in direct_walk_in | direct_website | ota."""
    if channel_ota:
        return "ota", OTA_LABELS.get(channel_ota, channel_ota.replace("_", " ").title())
    text = (notes or "").strip().lower()
    for code, label in OTA_LABELS.items():
        if label.lower() in text or code.replace("_", "") in text.replace(".", "").replace(" ", ""):
            return "ota", label
    if "website" in text or "online" in text or "guest portal" in text or "web booking" in text:
        return "direct_website", "Direct Website"
    return "direct_walk_in", "Direct Walk-In"


def _booking_sources(conn, tenant_id: str, start: date, end: date) -> dict:
    channel_map = _channel_reservation_ids(conn, tenant_id)
    rows = conn.execute(
        """SELECT id, notes, rate, check_in, check_out, status
           FROM hospitalityclaw_reservation
           WHERE tenant_id = ?
             AND check_in < ? AND check_out > ?
             AND status NOT IN ('cancelled')""",
        (tenant_id, end.isoformat(), start.isoformat()),
    ).fetchall()
    buckets = {
        "direct_walk_in": {"key": "direct_walk_in", "label": "Direct Walk-Ins", "bookings": 0, "room_nights": 0, "revenue": Decimal("0")},
        "direct_website": {"key": "direct_website", "label": "Direct Website", "bookings": 0, "room_nights": 0, "revenue": Decimal("0")},
        "ota": {"key": "ota", "label": "OTAs", "bookings": 0, "room_nights": 0, "revenue": Decimal("0")},
    }
    ota_detail: dict[str, dict] = {}
    for row in rows:
        rid = str(_g(row, "id"))
        bucket, label = _classify_source(_g(row, "notes"), channel_map.get(rid))
        try:
            cin = date.fromisoformat(str(_g(row, "check_in"))[:10])
            cout = date.fromisoformat(str(_g(row, "check_out"))[:10])
            nights = max(1, (cout - cin).days)
        except Exception:
            nights = 1
        rate = _dec(_g(row, "rate"))
        revenue = rate * Decimal(nights)
        buckets[bucket]["bookings"] += 1
        buckets[bucket]["room_nights"] += nights
        buckets[bucket]["revenue"] += revenue
        if bucket == "ota":
            detail = ota_detail.setdefault(label, {"label": label, "bookings": 0, "revenue": Decimal("0")})
            detail["bookings"] += 1
            detail["revenue"] += revenue

    total_bookings = sum(b["bookings"] for b in buckets.values()) or 1
    sources = []
    for key in ("direct_walk_in", "direct_website", "ota"):
        b = buckets[key]
        sources.append({
            "key": key,
            "label": b["label"],
            "bookings": b["bookings"],
            "room_nights": b["room_nights"],
            "revenue": _money(b["revenue"]),
            "share_pct": _pct(Decimal(b["bookings"]) / Decimal(total_bookings) * 100),
        })
    return {
        "sources": sources,
        "ota_breakdown": [
            {
                "label": d["label"],
                "bookings": d["bookings"],
                "revenue": _money(d["revenue"]),
            }
            for d in sorted(ota_detail.values(), key=lambda x: x["bookings"], reverse=True)
        ],
    }


def _series_for_month(conn, tenant_id: str, month_day: date) -> dict:
    start, end = _month_bounds(month_day)
    today = date.today()
    # For the current month, only include through today
    if start.year == today.year and start.month == today.month:
        end = min(end, today + timedelta(days=1))
    kpis = _period_kpis(conn, tenant_id, start, end)
    return {
        "label": start.strftime("%b %Y"),
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "room_revenue": kpis["room_revenue"],
        "occupancy_pct": kpis["occupancy_pct"],
        "adr": kpis["adr"],
        "revpar": kpis["revpar"],
        "daily": kpis["daily"],
    }


def _align_revenue_trend(current: dict, previous: dict, last_year: dict) -> list[dict]:
    """Align by day-of-month for chart overlays."""
    by_day: dict[int, dict] = {}
    for series_key, series in (
        ("current", current),
        ("previous_month", previous),
        ("last_year", last_year),
    ):
        for row in series.get("daily") or []:
            try:
                day_num = int(str(row["date"])[8:10])
            except Exception:
                continue
            slot = by_day.setdefault(day_num, {"day": day_num})
            slot[series_key] = float(row.get("room_revenue") or 0)
    return [by_day[k] for k in sorted(by_day)]


def _branch_row(conn, tenant_id: str, start: date, end: date) -> dict:
    meta = _hotel_meta(conn, tenant_id)
    kpis = _period_kpis(conn, tenant_id, start, end)
    return {
        "property_id": tenant_id,
        "hotel_id": meta["hotel_id"],
        "hotel_name": meta["hotel_name"],
        "currency": meta["currency"],
        "room_revenue": kpis["room_revenue"],
        "occupancy_pct": kpis["occupancy_pct"],
        "adr": kpis["adr"],
        "revpar": kpis["revpar"],
        "alos": kpis["alos"],
        "rooms_available": kpis["rooms_available"],
        "rooms_sold": kpis["rooms_sold"],
        "cancellation_rate_pct": kpis["cancellation_rate_pct"],
    }


def list_group_property_ids(group_id: str | None, fallback: list[str] | None = None) -> list[str]:
    if not group_id:
        return list(fallback or [])
    conn = _erp()
    try:
        from property_scope import list_properties_in_group, ensure_group_schema_erp
        ensure_group_schema_erp(conn)
        props = list_properties_in_group(conn, group_id) or []
        ids = []
        for p in props:
            pid = p.get("property_id") or p.get("id") or p.get("company_id")
            if pid:
                ids.append(str(pid))
        return ids or list(fallback or [])
    except Exception:
        return list(fallback or [])
    finally:
        conn.close()


def performance(
    tenant_id: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    property_ids: list[str] | None = None,
    today: date | None = None,
) -> dict:
    today = today or date.today()
    start, end = resolve_range(from_date, to_date, today=today)
    conn = _erp()
    try:
        meta = _hotel_meta(conn, tenant_id)
        kpis = _period_kpis(conn, tenant_id, start, end)
        sources = _booking_sources(conn, tenant_id, start, end)

        current_month = _series_for_month(conn, tenant_id, today)
        prev_anchor = (today.replace(day=1) - timedelta(days=1))
        previous_month = _series_for_month(conn, tenant_id, prev_anchor)
        try:
            last_year_day = today.replace(year=today.year - 1)
        except ValueError:
            last_year_day = today.replace(year=today.year - 1, day=28)
        last_year = _series_for_month(conn, tenant_id, last_year_day)
        trend = _align_revenue_trend(current_month, previous_month, last_year)

        branch_ids = []
        for pid in property_ids or [tenant_id]:
            if pid and pid not in branch_ids:
                branch_ids.append(pid)
        if tenant_id not in branch_ids:
            branch_ids.insert(0, tenant_id)

        branches = []
        for pid in branch_ids:
            try:
                branches.append(_branch_row(conn, pid, start, end))
            except Exception:
                log.exception("Branch metrics failed property=%s", pid)

        occupancy_table = kpis["daily"]
        return {
            "status": "ok",
            "hotel": meta,
            "period": {
                "from_date": start.isoformat(),
                "to_date": end.isoformat(),
                "label": f"{start.isoformat()} → {end.isoformat()}",
            },
            "kpis": {
                "room_revenue": kpis["room_revenue"],
                "rooms_available": kpis["rooms_available"],
                "available_room_nights": kpis["available_room_nights"],
                "rooms_sold": kpis["rooms_sold"],
                "occupancy_pct": kpis["occupancy_pct"],
                "adr": kpis["adr"],
                "revpar": kpis["revpar"],
                "alos": kpis["alos"],
                "cancellation_rate_pct": kpis["cancellation_rate_pct"],
                "cancellations": kpis["cancellations"],
                "reservations_created": kpis["reservations_created"],
            },
            "revenue_trend": {
                "current_month": current_month,
                "previous_month": previous_month,
                "last_year": last_year,
                "aligned": trend,
            },
            "booking_sources": sources,
            "branches": branches,
            "occupancy_table": occupancy_table,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
    finally:
        conn.close()


def export_csv(payload: dict) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    hotel = payload.get("hotel") or {}
    kpis = payload.get("kpis") or {}
    period = payload.get("period") or {}
    writer.writerow(["HotelPilot Analytics Export"])
    writer.writerow(["Hotel", hotel.get("hotel_name") or ""])
    writer.writerow(["Currency", hotel.get("currency") or "USD"])
    writer.writerow(["Period", period.get("label") or ""])
    writer.writerow([])
    writer.writerow(["KPI", "Value"])
    for key, label in (
        ("room_revenue", "Room revenue"),
        ("revpar", "RevPAR"),
        ("adr", "ADR"),
        ("occupancy_pct", "Occupancy %"),
        ("alos", "ALOS (nights)"),
        ("cancellation_rate_pct", "Cancellation rate %"),
        ("rooms_sold", "Rooms sold (room-nights)"),
        ("available_room_nights", "Available room-nights"),
        ("rooms_available", "Sellable rooms"),
    ):
        writer.writerow([label, kpis.get(key, "")])

    writer.writerow([])
    writer.writerow(["Occupancy by night"])
    writer.writerow(["Date", "Room revenue", "Rooms sold", "Rooms available", "Occupancy %"])
    for row in payload.get("occupancy_table") or []:
        writer.writerow([
            row.get("date"),
            row.get("room_revenue"),
            row.get("rooms_sold"),
            row.get("rooms_available"),
            row.get("occupancy_pct"),
        ])

    writer.writerow([])
    writer.writerow(["Booking sources"])
    writer.writerow(["Source", "Bookings", "Room nights", "Est. revenue", "Share %"])
    for src in (payload.get("booking_sources") or {}).get("sources") or []:
        writer.writerow([
            src.get("label"),
            src.get("bookings"),
            src.get("room_nights"),
            src.get("revenue"),
            src.get("share_pct"),
        ])

    writer.writerow([])
    writer.writerow(["Branch comparison"])
    writer.writerow(["Branch", "Room revenue", "Occupancy %", "ADR", "RevPAR", "ALOS", "Cancel %"])
    for branch in payload.get("branches") or []:
        writer.writerow([
            branch.get("hotel_name"),
            branch.get("room_revenue"),
            branch.get("occupancy_pct"),
            branch.get("adr"),
            branch.get("revpar"),
            branch.get("alos"),
            branch.get("cancellation_rate_pct"),
        ])
    return buf.getvalue().encode("utf-8")


def _esc_pdf(text: str) -> str:
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def export_pdf(payload: dict) -> bytes:
    hotel = payload.get("hotel") or {}
    kpis = payload.get("kpis") or {}
    period = payload.get("period") or {}
    currency = hotel.get("currency") or "USD"
    lines = [
        f"{hotel.get('hotel_name') or 'Hotel'} — Performance Analytics",
        f"Period: {period.get('label') or '—'}",
        f"Generated: {payload.get('generated_at') or '—'}",
        "",
        "Key metrics",
        "-" * 56,
        f"Room revenue:       {kpis.get('room_revenue')} {currency}",
        f"RevPAR:             {kpis.get('revpar')} {currency}",
        f"ADR:                {kpis.get('adr')} {currency}",
        f"Occupancy:          {kpis.get('occupancy_pct')}%",
        f"ALOS:               {kpis.get('alos')} nights",
        f"Cancellation rate:  {kpis.get('cancellation_rate_pct')}%",
        f"Rooms sold:         {kpis.get('rooms_sold')} room-nights",
        f"Available:          {kpis.get('available_room_nights')} room-nights",
        "",
        "Booking sources",
        "-" * 56,
    ]
    for src in (payload.get("booking_sources") or {}).get("sources") or []:
        lines.append(
            f"{src.get('label')}: {src.get('bookings')} bookings "
            f"({src.get('share_pct')}%) · {src.get('revenue')} {currency}"
        )
    lines.extend(["", "Branch comparison", "-" * 56])
    for branch in payload.get("branches") or []:
        lines.append(
            f"{branch.get('hotel_name')}: Rev {branch.get('room_revenue')} · "
            f"Occ {branch.get('occupancy_pct')}% · RevPAR {branch.get('revpar')}"
        )
    lines.extend(["", "Occupancy table (first 31 nights)", "-" * 56])
    for row in (payload.get("occupancy_table") or [])[:31]:
        lines.append(
            f"{row.get('date')}: sold {row.get('rooms_sold')}/{row.get('rooms_available')} "
            f"({row.get('occupancy_pct')}%) · {row.get('room_revenue')} {currency}"
        )
    lines.append("")
    lines.append("HotelPilot Business Intelligence")

    y = 780
    content_ops = ["BT", "/F1 10 Tf", "12 TL"]
    first = True
    for line in lines:
        safe = _esc_pdf(line)[:110]
        if first:
            content_ops.append(f"1 0 0 1 36 {y} Tm ({safe}) Tj")
            first = False
        else:
            content_ops.append("T*")
            content_ops.append(f"({safe}) Tj")
    content_ops.append("ET")
    content = "\n".join(content_ops).encode("latin-1", errors="replace")

    objects: list[bytes] = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
        (
            b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n"
        ),
        (
            f"4 0 obj<< /Length {len(content)} >>stream\n".encode("ascii")
            + content
            + b"\nendstream\nendobj\n"
        ),
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(out)
