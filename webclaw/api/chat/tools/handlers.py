"""Tool handler implementations backed by hospitalityclaw + admin analytics."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("webclaw.chat.tools")


def _tenant_scope(context: dict[str, Any]) -> dict[str, Any]:
    tenant = context.get("tenant") or {}
    hotel_id = (
        context.get("hotel_id")
        or context.get("current_branch")
        or tenant.get("active_property_id")
        or tenant.get("tenant_id")
    )
    if hotel_id and str(hotel_id).upper() == "ALL":
        hotel_id = None
    return {
        "tenant_id": hotel_id or tenant.get("tenant_id"),
        "is_superadmin": bool(tenant.get("is_superadmin") and not tenant.get("impersonating")),
        "group_id": tenant.get("group_id") or context.get("group_id"),
        "property_ids": list(tenant.get("assigned_properties") or []),
        "scope_all": bool(tenant.get("scope_all_properties")),
    }


async def _exec(
    action: str,
    params: dict[str, Any],
    context: dict[str, Any],
    *,
    skill: str = "hospitalityclaw",
) -> dict[str, Any]:
    from skills.executor import execute_skill

    scope = _tenant_scope(context)
    return await execute_skill(
        skill,
        action,
        params,
        tenant_id=None if scope["scope_all"] else scope["tenant_id"],
        is_superadmin=scope["is_superadmin"],
        group_id=scope["group_id"] if scope["scope_all"] else None,
        property_ids=scope["property_ids"] if scope["scope_all"] else None,
    )


def _parse_date(raw: str | None) -> str:
    if raw:
        try:
            return date.fromisoformat(raw[:10]).isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).date().isoformat()


async def get_daily_summary(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    day = _parse_date(args.get("date"))
    # status uses exclusive to_date → next day
    next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    hotel_id = args.get("hotel_id") or _tenant_scope(context)["tenant_id"]
    params: dict[str, Any] = {"from_date": day, "to_date": next_day}
    if hotel_id:
        params["hotel_id"] = hotel_id

    result = await _exec("status", params, context)
    if result.get("status") != "ok":
        return {
            "ok": False,
            "error": result.get("message") or "Failed to load daily summary",
            "display": {"type": "status", "title": "Daily summary failed", "body": result.get("message")},
        }

    data = result.get("data") or result
    rooms = data.get("rooms") or {}
    reservations = data.get("reservations") or {}
    occupied = rooms.get("occupied") or rooms.get("dirty") or 0
    if isinstance(rooms, dict) and "by_status" in rooms:
        by = rooms["by_status"] or {}
        occupied = int(by.get("occupied") or by.get("dirty") or occupied or 0)
        maintenance = int(
            by.get("maintenance")
            or by.get("out_of_order")
            or by.get("ooo")
            or 0
        )
    else:
        maintenance = int(rooms.get("maintenance") or rooms.get("out_of_order") or 0)

    pending_checkins = int(
        reservations.get("arrivals")
        or reservations.get("pending_check_in")
        or reservations.get("confirmed")
        or 0
    )
    revenue = float(data.get("revenue") or data.get("charges_total") or 0)
    currency = data.get("currency") or "USD"

    metrics = [
        {"label": "Revenue", "value": f"{currency} {revenue:,.2f}"},
        {"label": "Occupied rooms", "value": str(occupied)},
        {"label": "Pending check-ins", "value": str(pending_checkins)},
        {"label": "Maintenance", "value": str(maintenance)},
    ]
    return {
        "ok": True,
        "date": day,
        "hotel_id": hotel_id,
        "metrics": metrics,
        "raw": {
            "revenue": revenue,
            "occupied_rooms": occupied,
            "pending_check_ins": pending_checkins,
            "maintenance_issues": maintenance,
            "currency": currency,
        },
        "display": {
            "type": "metrics",
            "title": f"Daily summary — {day}",
            "metrics": metrics,
        },
    }


async def search_reservations(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    guest_name = (args.get("guest_name") or "").strip()
    room_number = (args.get("room_number") or "").strip()

    guest_ids: set[str] = set()
    if guest_name:
        guests = await _exec("list-guests", {"search": guest_name, "limit": 25}, context)
        if guests.get("status") == "ok":
            rows = (guests.get("data") or guests).get("guests") or guests.get("items") or []
            if isinstance(guests.get("data"), list):
                rows = guests["data"]
            for g in rows:
                gid = g.get("id")
                if gid:
                    guest_ids.add(str(gid))

    res = await _exec("list-reservations", {"limit": 50}, context)
    if res.get("status") != "ok":
        return {
            "ok": False,
            "error": res.get("message") or "Reservation search failed",
            "display": {
                "type": "status",
                "title": "Search failed",
                "body": res.get("message") or "Could not load reservations",
            },
        }

    data = res.get("data") or res
    items = data.get("reservations") or data.get("items") or []
    if isinstance(data, list):
        items = data

    matches: list[dict[str, Any]] = []
    for r in items:
        gname = (
            r.get("guest_name")
            or f"{r.get('guest_first_name', '')} {r.get('guest_last_name', '')}".strip()
            or r.get("guest_email")
            or ""
        )
        room = str(r.get("room_number") or r.get("room_no") or r.get("room") or "")
        gid = str(r.get("guest_id") or "")
        name_ok = True
        room_ok = True
        if guest_name:
            name_ok = guest_name.lower() in gname.lower() or (gid and gid in guest_ids)
        if room_number:
            room_ok = room_number.lower() in room.lower()
        if name_ok and room_ok:
            matches.append(
                {
                    "id": r.get("id"),
                    "guest": gname or "—",
                    "room": room or "—",
                    "status": r.get("status") or "—",
                    "check_in": r.get("check_in") or r.get("check_in_date") or "—",
                    "check_out": r.get("check_out") or r.get("check_out_date") or "—",
                }
            )

    if not matches and not guest_name and not room_number:
        matches = [
            {
                "id": r.get("id"),
                "guest": r.get("guest_name") or "—",
                "room": str(r.get("room_number") or "—"),
                "status": r.get("status") or "—",
                "check_in": r.get("check_in") or "—",
                "check_out": r.get("check_out") or "—",
            }
            for r in items[:15]
        ]

    columns = ["Guest", "Room", "Status", "Check-in", "Check-out"]
    rows = [
        [m["guest"], m["room"], m["status"], m["check_in"], m["check_out"]]
        for m in matches[:20]
    ]
    return {
        "ok": True,
        "count": len(matches),
        "reservations": matches[:20],
        "display": {
            "type": "table",
            "title": f"Reservations ({len(matches)})",
            "columns": columns,
            "rows": rows,
            "empty": "No matching reservations found.",
        },
    }


async def create_maintenance_ticket(
    args: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    room_ref = str(args.get("room_id") or "").strip()
    description = (args.get("description") or "").strip()
    if not room_ref or not description:
        return {
            "ok": False,
            "error": "room_id and description are required",
            "display": {
                "type": "status",
                "title": "Missing fields",
                "body": "Provide a room id/number and a description.",
            },
        }

    room_id = room_ref
    # Resolve room number → id when needed
    if len(room_ref) < 32:
        rooms = await _exec("list-rooms", {"limit": 200}, context)
        if rooms.get("status") == "ok":
            data = rooms.get("data") or rooms
            items = data.get("rooms") or data.get("items") or []
            if isinstance(data, list):
                items = data
            for room in items:
                num = str(room.get("number") or room.get("room_number") or "")
                if num.lower() == room_ref.lower() or str(room.get("id")) == room_ref:
                    room_id = str(room.get("id"))
                    break

    result = await _exec(
        "update-room-status",
        {
            "id": room_id,
            "status": "maintenance",
            "notes": description,
        },
        context,
    )
    if result.get("status") != "ok":
        # Retry without notes if script rejects unknown flag
        result = await _exec(
            "update-room-status",
            {"id": room_id, "status": "maintenance"},
            context,
        )

    if result.get("status") != "ok":
        return {
            "ok": False,
            "error": result.get("message") or "Could not create maintenance ticket",
            "display": {
                "type": "status",
                "title": "Maintenance ticket failed",
                "body": result.get("message") or "Update room status failed",
            },
        }

    return {
        "ok": True,
        "room_id": room_id,
        "description": description,
        "display": {
            "type": "status",
            "title": "Maintenance ticket created",
            "body": (
                f"Room `{room_ref}` marked for maintenance.\n\n"
                f"**Issue:** {description}"
            ),
            "tone": "success",
        },
    }


def _collect_platform_snapshot() -> dict[str, Any]:
    """Lightweight platform KPIs + health (no HTTP / permission wrapper)."""
    import db
    from admin_platform import (
        TIER_TO_PLAN,
        _erp,
        _load_plan_map,
        _scalar,
        _table_exists,
    )

    started = datetime.now(timezone.utc)
    web_conn = db.get_connection()
    plans = _load_plan_map(web_conn)
    erp = _erp()

    hotels_active = 0
    hotels_total = 0
    booking_revenue = 0.0
    total_rooms = 0
    tenants_for_mrr: list[str] = []
    db_ok = True

    if erp:
        try:
            if _table_exists(erp, "pms_tenant"):
                hotels_total = int(_scalar(erp, "SELECT COUNT(*) FROM pms_tenant"))
                hotels_active = int(
                    _scalar(
                        erp,
                        "SELECT COUNT(*) FROM pms_tenant WHERE status = 'active'",
                    )
                )
                try:
                    for r in erp.execute(
                        "SELECT subscription_tier FROM pms_tenant WHERE status = 'active'"
                    ).fetchall():
                        tier = r["subscription_tier"] if isinstance(r, dict) else r[0]
                        tenants_for_mrr.append(str(tier or "starter"))
                except Exception:
                    pass
            if _table_exists(erp, "hospitalityclaw_room"):
                total_rooms = int(
                    _scalar(erp, "SELECT COUNT(*) FROM hospitalityclaw_room")
                )
            if _table_exists(erp, "hospitalityclaw_folio_charge"):
                booking_revenue = float(
                    _scalar(
                        erp,
                        """SELECT COALESCE(SUM(CAST(amount AS REAL)), 0)
                           FROM hospitalityclaw_folio_charge
                           WHERE charge_type != 'payment'""",
                    )
                )
        except Exception:
            db_ok = False
        finally:
            try:
                erp.close()
            except Exception:
                pass
    else:
        db_ok = False

    subscription_mrr = 0.0
    for tier in tenants_for_mrr:
        code = TIER_TO_PLAN.get(str(tier).lower(), "starter")
        plan = plans.get(code) or {}
        try:
            subscription_mrr += float(plan.get("monthly_price") or 0)
        except (TypeError, ValueError):
            pass

    elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    health_status = "operational" if db_ok and elapsed_ms < 2000 else "degraded"
    return {
        "active_hotels": hotels_active,
        "total_hotels": hotels_total,
        "active_subscriptions": hotels_active,
        "subscription_mrr": round(subscription_mrr, 2),
        "revenue": round(booking_revenue, 2),
        "total_rooms": total_rooms,
        "health": {
            "database": "ok" if db_ok else "down",
            "api_latency_ms": elapsed_ms,
            "workers": "ok" if db_ok else "degraded",
            "status": health_status,
            "label": (
                "All Systems Operational"
                if health_status == "operational"
                else "Degraded Performance"
            ),
        },
    }


async def get_tenant_metrics(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    timeframe = (args.get("timeframe") or "all").lower()
    try:
        analytics = _collect_platform_snapshot()
    except Exception as e:
        log.exception("get_tenant_metrics failed")
        return {
            "ok": False,
            "error": str(e),
            "display": {
                "type": "status",
                "title": "Tenant metrics unavailable",
                "body": str(e)[:200],
            },
        }

    metrics = [
        {"label": "Active properties", "value": str(analytics.get("active_hotels") or 0)},
        {
            "label": "Active subscriptions",
            "value": str(analytics.get("active_subscriptions") or 0),
        },
        {
            "label": "Subscription MRR",
            "value": f"${float(analytics.get('subscription_mrr') or 0):,.2f}",
        },
        {
            "label": "Booking revenue",
            "value": f"${float(analytics.get('revenue') or 0):,.2f}",
        },
        {"label": "Total rooms", "value": str(analytics.get("total_rooms") or 0)},
    ]
    return {
        "ok": True,
        "timeframe": timeframe,
        "metrics": metrics,
        "display": {
            "type": "metrics",
            "title": f"Tenant metrics ({timeframe})",
            "metrics": metrics,
        },
    }


async def get_system_health(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    try:
        analytics = _collect_platform_snapshot()
    except Exception as e:
        log.exception("get_system_health failed")
        return {
            "ok": False,
            "error": str(e),
            "display": {
                "type": "status",
                "title": "System health unavailable",
                "body": str(e)[:200],
            },
        }

    health = analytics.get("health") or {}
    metrics = [
        {"label": "Overall", "value": str(health.get("label") or health.get("status") or "—")},
        {"label": "Database", "value": str(health.get("database") or "—")},
        {"label": "Workers", "value": str(health.get("workers") or "—")},
        {"label": "API latency", "value": f"{health.get('api_latency_ms', '—')} ms"},
    ]
    sync_errors = []
    if health.get("database") == "down":
        sync_errors.append("Database connectivity failed")
    if health.get("workers") == "degraded":
        sync_errors.append("Background workers degraded")
    if health.get("status") == "degraded":
        sync_errors.append("Platform performance degraded")

    rows = [[e] for e in sync_errors] if sync_errors else [["No sync errors reported"]]
    return {
        "ok": True,
        "health": health,
        "display": {
            "type": "metrics",
            "title": "System health",
            "metrics": metrics,
            "footer_table": {
                "type": "table",
                "title": "Sync / errors",
                "columns": ["Detail"],
                "rows": rows,
            },
        },
    }


HANDLERS = {
    "get_daily_summary": get_daily_summary,
    "search_reservations": search_reservations,
    "create_maintenance_ticket": create_maintenance_ticket,
    "get_tenant_metrics": get_tenant_metrics,
    "get_system_health": get_system_health,
}
