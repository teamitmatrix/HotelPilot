"""P1 hospitality reports — channel production, commission, pickup, forecast, cancels."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from services import analytics as ax


def _dec(val) -> Decimal:
    try:
        return Decimal(str(val or "0"))
    except Exception:
        return Decimal("0")


def _money(val) -> str:
    return str(_dec(val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _pct(val) -> str:
    return str(_dec(val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _nights(check_in, check_out) -> int:
    try:
        cin = date.fromisoformat(str(check_in)[:10])
        cout = date.fromisoformat(str(check_out)[:10])
        return max(1, (cout - cin).days)
    except Exception:
        return 1


def _resolve_range(from_date: str | None, to_date: str | None) -> tuple[date, date]:
    today = date.today()
    start = date.fromisoformat(from_date) if from_date else today.replace(day=1)
    end = date.fromisoformat(to_date) if to_date else today
    if end < start:
        raise ValueError("to_date must be on or after from_date")
    return start, end


def channel_production(
    tenant_id: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """Bookings, room-nights, revenue, ADR by OTA vs direct for overlapping stays."""
    start, end = _resolve_range(from_date, to_date)
    conn = ax._erp()
    try:
        try:
            from hospitalityclaw.init_db import init_db
            init_db(conn)
        except Exception:
            pass
        meta = ax._hotel_meta(conn, tenant_id)
        channel_map = ax._channel_reservation_ids(conn, tenant_id)
        rows = conn.execute(
            """SELECT id, status, check_in, check_out, rate, notes, created_at
               FROM hospitalityclaw_reservation
               WHERE tenant_id = ?
                 AND status NOT IN ('cancelled')
                 AND check_in < ? AND check_out > ?""",
            (tenant_id, (end + timedelta(days=1)).isoformat(), start.isoformat()),
        ).fetchall()

        buckets: dict[str, dict[str, Any]] = {}
        for row in rows:
            rid = str(ax._g(row, "id"))
            ota = channel_map.get(rid)
            bucket, label = ax._classify_source(ax._g(row, "notes"), ota)
            key = f"{bucket}:{label}"
            slot = buckets.setdefault(
                key,
                {
                    "bucket": bucket,
                    "label": label,
                    "ota": ota if bucket == "ota" else None,
                    "bookings": 0,
                    "room_nights": 0,
                    "revenue": Decimal("0"),
                },
            )
            nights = _nights(ax._g(row, "check_in"), ax._g(row, "check_out"))
            # Overlap nights within window
            try:
                cin = date.fromisoformat(str(ax._g(row, "check_in"))[:10])
                cout = date.fromisoformat(str(ax._g(row, "check_out"))[:10])
                overlap_start = max(cin, start)
                overlap_end = min(cout, end + timedelta(days=1))
                nights = max(0, (overlap_end - overlap_start).days) or nights
            except Exception:
                pass
            slot["bookings"] += 1
            slot["room_nights"] += nights
            slot["revenue"] += _dec(ax._g(row, "rate")) * Decimal(nights)

        channels = []
        for slot in sorted(buckets.values(), key=lambda s: s["revenue"], reverse=True):
            rn = int(slot["room_nights"])
            rev = slot["revenue"]
            adr = (rev / Decimal(rn)) if rn else Decimal("0")
            channels.append({
                "bucket": slot["bucket"],
                "label": slot["label"],
                "ota": slot["ota"],
                "bookings": slot["bookings"],
                "room_nights": rn,
                "revenue": _money(rev),
                "adr": _money(adr),
            })

        total_rev = sum((_dec(c["revenue"]) for c in channels), Decimal("0"))
        total_rn = sum(int(c["room_nights"]) for c in channels)
        total_bookings = sum(int(c["bookings"]) for c in channels)
        return {
            "status": "ok",
            "hotel": meta,
            "period": {
                "from_date": start.isoformat(),
                "to_date": end.isoformat(),
            },
            "summary": {
                "bookings": total_bookings,
                "room_nights": total_rn,
                "revenue": _money(total_rev),
                "adr": _money((total_rev / Decimal(total_rn)) if total_rn else 0),
            },
            "channels": channels,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        conn.close()


def commission_estimate(
    tenant_id: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """Estimate OTA commission from channel production × connection commission %."""
    prod = channel_production(tenant_id, from_date=from_date, to_date=to_date)
    conn = ax._erp()
    try:
        try:
            from services import channel_manager as cm
            cm.ensure_schema(conn)
        except Exception:
            pass
        rates = {}
        try:
            rows = conn.execute(
                """SELECT ota, commission_percent FROM hospitalityclaw_channel_connection
                   WHERE tenant_id = ?""",
                (tenant_id,),
            ).fetchall()
            for r in rows:
                try:
                    rates[str(ax._g(r, "ota"))] = float(ax._g(r, "commission_percent") or 0)
                except Exception:
                    rates[str(ax._g(r, "ota"))] = 0.0
        except Exception:
            rates = {}

        lines = []
        total_comm = Decimal("0")
        total_rev = Decimal("0")
        for ch in prod.get("channels") or []:
            if ch.get("bucket") != "ota":
                continue
            ota = ch.get("ota") or ""
            pct = Decimal(str(rates.get(ota, 0)))
            rev = _dec(ch.get("revenue"))
            comm = (rev * pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_comm += comm
            total_rev += rev
            lines.append({
                **ch,
                "commission_percent": _pct(pct),
                "commission_estimate": _money(comm),
                "net_revenue": _money(rev - comm),
            })

        return {
            "status": "ok",
            "hotel": prod.get("hotel"),
            "period": prod.get("period"),
            "summary": {
                "ota_revenue": _money(total_rev),
                "commission_estimate": _money(total_comm),
                "net_revenue": _money(total_rev - total_comm),
                "channels_with_rate": sum(1 for v in rates.values() if v > 0),
            },
            "commission_rates": [
                {"ota": k, "ota_label": ax.OTA_LABELS.get(k, k), "commission_percent": _pct(v)}
                for k, v in sorted(rates.items())
            ],
            "channels": lines,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        conn.close()


def pickup_pace(
    tenant_id: str,
    *,
    as_of: str | None = None,
    days: int = 30,
) -> dict:
    """On-the-books room-nights for the next N days vs same window last year."""
    days = max(1, min(90, int(days or 30)))
    as_of_d = date.fromisoformat(as_of) if as_of else date.today()
    ly = date(as_of_d.year - 1, as_of_d.month, min(as_of_d.day, 28 if as_of_d.month == 2 else as_of_d.day))
    try:
        ly = as_of_d.replace(year=as_of_d.year - 1)
    except ValueError:
        ly = as_of_d - timedelta(days=365)

    conn = ax._erp()
    try:
        try:
            from hospitalityclaw.init_db import init_db
            init_db(conn)
        except Exception:
            pass
        meta = ax._hotel_meta(conn, tenant_id)
        available = ax._available_rooms(conn, tenant_id)

        def curve(anchor: date) -> list[dict]:
            out = []
            for i in range(days):
                night = anchor + timedelta(days=i)
                sold = ax._sold_on_night(conn, tenant_id, night)
                out.append({
                    "offset": i,
                    "date": night.isoformat(),
                    "rooms_sold": sold,
                    "rooms_available": available,
                    "occupancy_pct": _pct(
                        (Decimal(sold) / Decimal(available) * 100) if available else 0
                    ),
                })
            return out

        ty = curve(as_of_d)
        ly_curve = curve(ly)
        ty_rn = sum(r["rooms_sold"] for r in ty)
        ly_rn = sum(r["rooms_sold"] for r in ly_curve)
        aligned = []
        for i in range(days):
            aligned.append({
                "offset": i,
                "ty_date": ty[i]["date"],
                "ly_date": ly_curve[i]["date"],
                "ty_rooms_sold": ty[i]["rooms_sold"],
                "ly_rooms_sold": ly_curve[i]["rooms_sold"],
                "ty_occupancy_pct": ty[i]["occupancy_pct"],
                "ly_occupancy_pct": ly_curve[i]["occupancy_pct"],
                "variance": ty[i]["rooms_sold"] - ly_curve[i]["rooms_sold"],
            })

        return {
            "status": "ok",
            "hotel": meta,
            "as_of": as_of_d.isoformat(),
            "ly_as_of": ly.isoformat(),
            "days": days,
            "summary": {
                "ty_room_nights": ty_rn,
                "ly_room_nights": ly_rn,
                "variance": ty_rn - ly_rn,
                "pace_pct": _pct(
                    ((Decimal(ty_rn) / Decimal(ly_rn) * 100) if ly_rn else 0)
                ),
                "rooms_available": available,
            },
            "daily": aligned,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        conn.close()


def forward_forecast(
    tenant_id: str,
    *,
    days: int = 30,
    from_date: str | None = None,
) -> dict:
    """Occupancy + projected room revenue for next 30/90 days."""
    days = max(1, min(90, int(days or 30)))
    base = ax.expected_occupancy(tenant_id, days=days, from_date=from_date)
    conn = ax._erp()
    try:
        try:
            from hospitalityclaw.init_db import init_db
            init_db(conn)
        except Exception:
            pass
        daily = []
        rev_total = Decimal("0")
        for row in base.get("daily") or []:
            night = date.fromisoformat(row["date"])
            # Sum reservation rates overlapping the night
            stays = conn.execute(
                """SELECT rate, check_in, check_out FROM hospitalityclaw_reservation
                   WHERE tenant_id = ?
                     AND status IN ('reserved', 'pre_checked_in', 'checked_in', 'checked_out')
                     AND check_in <= ? AND check_out > ?""",
                (tenant_id, night.isoformat(), night.isoformat()),
            ).fetchall()
            night_rev = Decimal("0")
            for stay in stays:
                night_rev += _dec(ax._g(stay, "rate"))
            rev_total += night_rev
            avail = int(row.get("rooms_available") or 0)
            sold = int(row.get("rooms_sold") or 0)
            daily.append({
                **row,
                "projected_room_revenue": _money(night_rev),
                "projected_adr": _money((night_rev / Decimal(sold)) if sold else 0),
                "projected_revpar": _money((night_rev / Decimal(avail)) if avail else 0),
            })
        avail_nights = int((base.get("summary") or {}).get("available_room_nights") or 0)
        sold_nights = int((base.get("summary") or {}).get("room_nights_sold") or 0)
        return {
            "status": "ok",
            "hotel": base.get("hotel"),
            "period": base.get("period"),
            "summary": {
                **(base.get("summary") or {}),
                "projected_room_revenue": _money(rev_total),
                "projected_adr": _money(
                    (rev_total / Decimal(sold_nights)) if sold_nights else 0
                ),
                "projected_revpar": _money(
                    (rev_total / Decimal(avail_nights)) if avail_nights else 0
                ),
            },
            "daily": daily,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        conn.close()


def cancellations_by_source(
    tenant_id: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """Cancelled and no-show counts/revenue by booking source."""
    start, end = _resolve_range(from_date, to_date)
    conn = ax._erp()
    try:
        try:
            from hospitalityclaw.init_db import init_db
            init_db(conn)
        except Exception:
            pass
        meta = ax._hotel_meta(conn, tenant_id)
        channel_map = ax._channel_reservation_ids(conn, tenant_id)
        rows = conn.execute(
            """SELECT id, status, check_in, check_out, rate, notes, created_at, updated_at
               FROM hospitalityclaw_reservation
               WHERE tenant_id = ?
                 AND status IN ('cancelled', 'no_show')
                 AND (
                   (date(created_at) >= date(?) AND date(created_at) <= date(?))
                   OR (check_in >= ? AND check_in <= ?)
                 )""",
            (
                tenant_id,
                start.isoformat(),
                end.isoformat(),
                start.isoformat(),
                end.isoformat(),
            ),
        ).fetchall()

        buckets: dict[str, dict[str, Any]] = {}
        for row in rows:
            rid = str(ax._g(row, "id"))
            ota = channel_map.get(rid)
            bucket, label = ax._classify_source(ax._g(row, "notes"), ota)
            key = f"{bucket}:{label}"
            slot = buckets.setdefault(
                key,
                {
                    "bucket": bucket,
                    "label": label,
                    "ota": ota if bucket == "ota" else None,
                    "cancellations": 0,
                    "no_shows": 0,
                    "lost_room_nights": 0,
                    "lost_revenue": Decimal("0"),
                },
            )
            nights = _nights(ax._g(row, "check_in"), ax._g(row, "check_out"))
            lost = _dec(ax._g(row, "rate")) * Decimal(nights)
            status = str(ax._g(row, "status") or "")
            if status == "no_show":
                slot["no_shows"] += 1
            else:
                slot["cancellations"] += 1
            slot["lost_room_nights"] += nights
            slot["lost_revenue"] += lost

        sources = []
        for slot in sorted(buckets.values(), key=lambda s: s["lost_revenue"], reverse=True):
            sources.append({
                "bucket": slot["bucket"],
                "label": slot["label"],
                "ota": slot["ota"],
                "cancellations": slot["cancellations"],
                "no_shows": slot["no_shows"],
                "total": slot["cancellations"] + slot["no_shows"],
                "lost_room_nights": slot["lost_room_nights"],
                "lost_revenue": _money(slot["lost_revenue"]),
            })

        cancels = sum(s["cancellations"] for s in sources)
        noshows = sum(s["no_shows"] for s in sources)
        return {
            "status": "ok",
            "hotel": meta,
            "period": {"from_date": start.isoformat(), "to_date": end.isoformat()},
            "summary": {
                "cancellations": cancels,
                "no_shows": noshows,
                "total": cancels + noshows,
                "lost_revenue": _money(
                    sum((_dec(s["lost_revenue"]) for s in sources), Decimal("0"))
                ),
            },
            "sources": sources,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        conn.close()
