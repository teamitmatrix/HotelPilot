"""Automated Night Audit processing engine.

Posts room charges + taxes for in-house guests, marks no-shows with
policy penalties, closes the day's cash drawer, builds a KPI report
(PDF + JSON), and emails HOTEL_OWNER / ACCOUNTANT.
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import db
from jobs.night_audit_report import build_night_audit_pdf

log = logging.getLogger("webclaw.jobs.night_audit")


def _pms_root() -> str:
    try:
        from env_loader import pms_root
        return pms_root()
    except Exception:
        import os
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _ensure_hosp_path() -> None:
    root = _pms_root()
    if root not in sys.path:
        sys.path.insert(0, root)


def _erp():
    return db.get_skill_db("erpclaw") or db.get_skill_db("hospitalityclaw")


def _web():
    return db.get_connection()


def _init_hosp(conn) -> None:
    _ensure_hosp_path()
    from hospitalityclaw.init_db import init_db
    init_db(conn)


def _hq():
    """Import hospitality db_query helpers (after path ensure)."""
    _ensure_hosp_path()
    from hospitalityclaw.scripts import db_query as hq
    return hq


def _row_get(row, key, default=None):
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


def _dec(val) -> Decimal:
    try:
        return Decimal(str(val or "0"))
    except Exception:
        return Decimal("0")


def _money(val) -> str:
    return str(_dec(val).quantize(Decimal("0.01")))


def _parse_settings(raw) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _night_audit_policy(settings: dict) -> dict:
    block = settings.get("night_audit")
    if not isinstance(block, dict):
        block = {}
    # Flat fallbacks for simple hotel settings UIs
    return {
        "audit_hour": int(block.get("audit_hour") or settings.get("night_audit_hour") or 0),
        "no_show_fee_nights": float(
            block.get("no_show_fee_nights")
            if block.get("no_show_fee_nights") is not None
            else settings.get("no_show_fee_nights")
            if settings.get("no_show_fee_nights") is not None
            else 1
        ),
        "no_show_fee_percent": block.get("no_show_fee_percent")
        if block.get("no_show_fee_percent") is not None
        else settings.get("no_show_fee_percent"),
        "no_show_fee_fixed": block.get("no_show_fee_fixed")
        if block.get("no_show_fee_fixed") is not None
        else settings.get("no_show_fee_fixed"),
        "email_report": bool(
            block.get("email_report", settings.get("night_audit_email_report", True))
        ),
        "block_on_exceptions": bool(
            block.get("block_on_exceptions", settings.get("night_audit_block_on_exceptions", False))
        ),
    }


def hotel_local_now(tz_name: str | None) -> datetime:
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz)


def resolve_business_date(
    tz_name: str | None,
    *,
    explicit: str | None = None,
    for_scheduler: bool = False,
    audit_hour: int = 0,
) -> str:
    """Return ISO business date for the audit.

    Manual runs default to hotel-local today.
    Scheduler after midnight closes yesterday until mid-morning.
    """
    if explicit:
        return explicit
    now = hotel_local_now(tz_name)
    today = now.date()
    if for_scheduler and now.hour < max(6, int(audit_hour) + 6):
        return (today - timedelta(days=1)).isoformat()
    return today.isoformat()


def list_hotels_for_audit(tenant_id: str | None = None) -> list[dict]:
    conn = _erp()
    if not conn:
        return []
    try:
        _init_hosp(conn)
        if tenant_id:
            rows = conn.execute(
                """SELECT id, tenant_id, name, currency, timezone, settings_json
                   FROM hospitalityclaw_hotel WHERE tenant_id = ? ORDER BY name""",
                (tenant_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, tenant_id, name, currency, timezone, settings_json
                   FROM hospitalityclaw_hotel ORDER BY name"""
            ).fetchall()
        return [
            {
                "id": _row_get(r, "id"),
                "tenant_id": _row_get(r, "tenant_id"),
                "name": _row_get(r, "name"),
                "currency": _row_get(r, "currency") or "USD",
                "timezone": _row_get(r, "timezone") or "UTC",
                "settings_json": _row_get(r, "settings_json"),
            }
            for r in rows
        ]
    finally:
        conn.close()


def already_ran(tenant_id: str, hotel_id: str, business_date: str) -> bool:
    conn = _erp()
    if not conn:
        return False
    try:
        _init_hosp(conn)
        row = conn.execute(
            """SELECT id FROM hospitalityclaw_night_audit_run
               WHERE tenant_id = ? AND hotel_id = ? AND business_date = ?
                 AND status IN ('completed', 'completed_with_exceptions')
               LIMIT 1""",
            (tenant_id, hotel_id, business_date),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def _staff_finance_emails(tenant_id: str) -> list[dict]:
    """Emails for HOTEL_OWNER and ACCOUNTANT on this property."""
    conn = _web()
    try:
        rows = conn.execute(
            """SELECT DISTINCT u.email, u.full_name, r.code AS role_code
               FROM webclaw_user u
               JOIN webclaw_user_role ur ON ur.user_id = u.id
               JOIN webclaw_role r ON r.id = ur.role_id
               WHERE r.code IN ('HOTEL_OWNER', 'ACCOUNTANT')
                 AND COALESCE(u.status, 'active') = 'active'
                 AND u.email IS NOT NULL AND TRIM(u.email) != ''
                 AND (
                   u.tenant_id = ?
                   OR EXISTS (
                     SELECT 1 FROM webclaw_user_tenant ut
                     WHERE ut.user_id = u.id AND ut.tenant_id = ?
                   )
                 )
                 AND (ur.tenant_id IS NULL OR ur.tenant_id = ?)""",
            (tenant_id, tenant_id, tenant_id),
        ).fetchall()
        out = []
        seen = set()
        for r in rows:
            email = str(_row_get(r, "email") or "").strip().lower()
            if not email or email in seen:
                continue
            seen.add(email)
            out.append(
                {
                    "email": email,
                    "full_name": _row_get(r, "full_name"),
                    "role_code": _row_get(r, "role_code"),
                }
            )
        return out
    except Exception:
        log.exception("staff email lookup failed for tenant %s", tenant_id)
        return []
    finally:
        conn.close()


def _post_room_charges(conn, hq, tenant_id: str, hotel_id: str, business_date: str) -> dict:
    rows = conn.execute(
        """SELECT f.*, r.rate, r.id AS reservation_pk, r.status AS res_status,
                  r.guest_id AS res_guest, r.hotel_id AS res_hotel,
                  r.room_type_id AS res_room_type
           FROM hospitalityclaw_folio f
           JOIN hospitalityclaw_reservation r ON r.id = f.reservation_id
           WHERE f.tenant_id = ? AND f.status = 'open'
             AND r.status = 'checked_in' AND r.hotel_id = ?""",
        (tenant_id, hotel_id),
    ).fetchall()

    posted, skipped = [], []
    tax_count = 0
    gl_count = 0
    revenue = Decimal("0")

    for row in rows:
        folio = dict(row)
        res = {
            "id": row["reservation_pk"],
            "rate": row["rate"],
            "status": row["res_status"],
            "guest_id": row["res_guest"],
            "hotel_id": row["res_hotel"],
            "room_type_id": row["res_room_type"],
        }
        charge_id, already, gl_ids, taxes = hq._post_one_room_tariff(
            conn, tenant_id, folio, res, business_date,
        )
        if already:
            skipped.append({"folio_id": folio["id"], "charge_id": charge_id, "already_posted": True})
            continue
        amt_row = conn.execute(
            "SELECT amount FROM hospitalityclaw_folio_charge WHERE id = ?",
            (charge_id,),
        ).fetchone()
        revenue += _dec(_row_get(amt_row, "amount"))
        posted.append({
            "folio_id": folio["id"],
            "reservation_id": res["id"],
            "charge_id": charge_id,
            "gl_posted": bool(gl_ids),
            "taxes": taxes,
        })
        if gl_ids:
            gl_count += 1
        tax_count += len(taxes or [])

    return {
        "posted": posted,
        "skipped": skipped,
        "tax_lines": tax_count,
        "gl_posts": gl_count,
        "room_revenue": _money(revenue),
    }


def _no_show_penalty_amount(conn, hq, tenant_id, hotel, reservation, policy) -> Decimal:
    rate = _dec(reservation.get("rate") or "0")
    if hotel.get("id") and reservation.get("room_type_id"):
        try:
            rate = _dec(
                hq._resolve_nightly_rate(
                    conn,
                    tenant_id,
                    hotel["id"],
                    reservation["room_type_id"],
                    reservation.get("check_in") or date.today().isoformat(),
                    str(rate),
                )
            )
        except Exception:
            pass

    if policy.get("no_show_fee_fixed") not in (None, ""):
        return _dec(policy["no_show_fee_fixed"])
    if policy.get("no_show_fee_percent") not in (None, ""):
        pct = _dec(policy["no_show_fee_percent"])
        return (rate * pct / Decimal("100")).quantize(Decimal("0.01"))
    nights = Decimal(str(policy.get("no_show_fee_nights") or 1))
    if nights < 0:
        nights = Decimal("0")
    return (rate * nights).quantize(Decimal("0.01"))


def _process_no_shows(
    conn, hq, tenant_id: str, hotel: dict, business_date: str, policy: dict
) -> dict:
    """Mark overdue arrivals as no_show and post cancellation penalties."""
    rows = conn.execute(
        """SELECT r.*, f.id AS folio_id, f.status AS folio_status, f.balance AS folio_balance
           FROM hospitalityclaw_reservation r
           LEFT JOIN hospitalityclaw_folio f
             ON f.reservation_id = r.id AND f.tenant_id = r.tenant_id
           WHERE r.tenant_id = ? AND r.hotel_id = ?
             AND r.status IN ('reserved', 'pre_checked_in')
             AND r.check_in <= ?""",
        (tenant_id, hotel["id"], business_date),
    ).fetchall()

    marked = []
    penalties = []
    for row in rows:
        rid = row["id"]
        guest_id = row["guest_id"]
        note = f"[Night audit {business_date}] Marked no-show"
        existing = row["notes"] or ""
        notes = f"{existing}\n{note}".strip() if existing else note
        conn.execute(
            """UPDATE hospitalityclaw_reservation
               SET status = 'no_show', notes = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND tenant_id = ?""",
            (notes, rid, tenant_id),
        )

        folio_id = row["folio_id"]
        if not folio_id:
            folio_id = hq._open_folio(conn, tenant_id, rid, guest_id)

        amount = _no_show_penalty_amount(conn, hq, tenant_id, hotel, dict(row), policy)
        penalty_info = {
            "reservation_id": rid,
            "folio_id": folio_id,
            "amount": _money(amount),
        }
        if amount > 0:
            charge_id, gl_ids = hq._post_folio_line(
                conn,
                tenant_id,
                folio_id,
                f"No-show penalty ({business_date})",
                str(amount),
                "other",
                business_date,
            )
            taxes = hq._apply_tax_rules(
                conn, tenant_id, hotel["id"], folio_id, str(amount), "room", business_date,
            )
            penalty_info.update({
                "charge_id": charge_id,
                "gl_posted": bool(gl_ids),
                "taxes": taxes,
            })
            penalties.append(penalty_info)
        marked.append({"reservation_id": rid, "folio_id": folio_id})

    return {
        "marked": marked,
        "penalties": penalties,
        "count": len(marked),
        "penalty_count": len(penalties),
    }


def _close_cash_drawer(
    conn,
    tenant_id: str,
    hotel_id: str,
    business_date: str,
    *,
    run_id: str,
    closed_by: str,
) -> dict:
    """Balance payment totals for the business date and close the cash drawer day."""
    totals = {"cash": Decimal("0"), "card": Decimal("0"), "bank": Decimal("0"), "other": Decimal("0")}
    # Cast to text before substr so TIMESTAMPTZ columns on PostgreSQL work
    # the same as ISO text timestamps on SQLite.
    rows = conn.execute(
        """SELECT t.payment_method, t.amount
           FROM hospitalityclaw_transaction t
           JOIN hospitalityclaw_folio f ON f.id = t.folio_id AND f.tenant_id = t.tenant_id
           JOIN hospitalityclaw_reservation r ON r.id = f.reservation_id
           WHERE t.tenant_id = ? AND t.status = 'posted' AND r.hotel_id = ?
             AND substr(CAST(COALESCE(t.posted_at, t.created_at) AS TEXT), 1, 10) = ?""",
        (tenant_id, hotel_id, business_date),
    ).fetchall()
    for t in rows:
        method = str(_row_get(t, "payment_method") or "other").lower()
        if method not in totals:
            method = "other"
        totals[method] += _dec(_row_get(t, "amount"))

    opening = Decimal("0")
    existing = conn.execute(
        """SELECT * FROM hospitalityclaw_cash_drawer_day
           WHERE tenant_id = ? AND hotel_id = ? AND business_date = ?""",
        (tenant_id, hotel_id, business_date),
    ).fetchone()
    if existing:
        opening = _dec(_row_get(existing, "opening_balance"))

    expected_cash = opening + totals["cash"]
    closing = expected_cash
    variance = Decimal("0")
    drawer_id = _row_get(existing, "id") if existing else str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    notes = f"Closed by night audit {run_id}"

    if existing:
        conn.execute(
            """UPDATE hospitalityclaw_cash_drawer_day
               SET cash_received = ?, card_received = ?, bank_received = ?, other_received = ?,
                   expected_cash = ?, closing_balance = ?, variance = ?,
                   status = 'closed', closed_at = ?, closed_by = ?,
                   night_audit_run_id = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (
                _money(totals["cash"]),
                _money(totals["card"]),
                _money(totals["bank"]),
                _money(totals["other"]),
                _money(expected_cash),
                _money(closing),
                _money(variance),
                now,
                closed_by,
                run_id,
                notes,
                drawer_id,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO hospitalityclaw_cash_drawer_day
               (id, tenant_id, hotel_id, business_date, opening_balance,
                cash_received, card_received, bank_received, other_received,
                expected_cash, closing_balance, variance, status,
                closed_at, closed_by, night_audit_run_id, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'closed', ?, ?, ?, ?)""",
            (
                drawer_id,
                tenant_id,
                hotel_id,
                business_date,
                _money(opening),
                _money(totals["cash"]),
                _money(totals["card"]),
                _money(totals["bank"]),
                _money(totals["other"]),
                _money(expected_cash),
                _money(closing),
                _money(variance),
                now,
                closed_by,
                run_id,
                notes,
            ),
        )

    return {
        "id": drawer_id,
        "status": "closed",
        "opening_balance": _money(opening),
        "cash_received": _money(totals["cash"]),
        "card_received": _money(totals["card"]),
        "bank_received": _money(totals["bank"]),
        "other_received": _money(totals["other"]),
        "expected_cash": _money(expected_cash),
        "closing_balance": _money(closing),
        "variance": _money(variance),
    }


def collect_night_audit_exceptions(
    conn, tenant_id: str, hotel_id: str, business_date: str
) -> list[dict]:
    """Actionable queue: open unpaid folios, overstays, OOO rooms, pending arrivals."""
    exceptions: list[dict] = []

    # Open folios with unpaid balance (checked-in or departing)
    open_folios = conn.execute(
        """SELECT f.id AS folio_id, f.balance, f.status AS folio_status,
                  r.id AS reservation_id, r.status AS reservation_status,
                  r.check_in, r.check_out, r.room_id,
                  g.first_name, g.last_name, rm.room_number
           FROM hospitalityclaw_folio f
           JOIN hospitalityclaw_reservation r ON r.id = f.reservation_id
           LEFT JOIN hospitalityclaw_guest g ON g.id = f.guest_id
           LEFT JOIN hospitalityclaw_room rm ON rm.id = r.room_id
           WHERE f.tenant_id = ? AND r.hotel_id = ? AND f.status = 'open'
             AND CAST(f.balance AS REAL) != 0""",
        (tenant_id, hotel_id),
    ).fetchall()
    for row in open_folios:
        bal = _dec(_row_get(row, "balance"))
        severity = "warning" if bal > 0 else "info"
        guest = f"{_row_get(row, 'first_name') or ''} {_row_get(row, 'last_name') or ''}".strip()
        exceptions.append({
            "code": "open_folio_balance",
            "severity": severity if bal > 0 else "info",
            "title": "Open folio with balance",
            "message": (
                f"{guest or 'Guest'} folio balance {_money(bal)}"
                + (f" · room {_row_get(row, 'room_number')}" if _row_get(row, "room_number") else "")
            ),
            "folio_id": _row_get(row, "folio_id"),
            "reservation_id": _row_get(row, "reservation_id"),
            "reservation_status": _row_get(row, "reservation_status"),
            "balance": _money(bal),
            "room_number": _row_get(row, "room_number"),
            "href": "/app/hotel/billing",
        })

    # Still in-house past scheduled departure
    overstays = conn.execute(
        """SELECT r.id AS reservation_id, r.status, r.check_in, r.check_out, r.room_id,
                  f.id AS folio_id, f.balance,
                  g.first_name, g.last_name, rm.room_number
           FROM hospitalityclaw_reservation r
           LEFT JOIN hospitalityclaw_folio f
             ON f.reservation_id = r.id AND f.tenant_id = r.tenant_id AND f.status = 'open'
           LEFT JOIN hospitalityclaw_guest g ON g.id = r.guest_id
           LEFT JOIN hospitalityclaw_room rm ON rm.id = r.room_id
           WHERE r.tenant_id = ? AND r.hotel_id = ?
             AND r.status = 'checked_in' AND r.check_out <= ?""",
        (tenant_id, hotel_id, business_date),
    ).fetchall()
    for row in overstays:
        guest = f"{_row_get(row, 'first_name') or ''} {_row_get(row, 'last_name') or ''}".strip()
        exceptions.append({
            "code": "overstay",
            "severity": "error",
            "title": "Departure overdue",
            "message": (
                f"{guest or 'Guest'} still in-house past check-out "
                f"{_row_get(row, 'check_out')}"
                + (f" · room {_row_get(row, 'room_number')}" if _row_get(row, "room_number") else "")
            ),
            "folio_id": _row_get(row, "folio_id"),
            "reservation_id": _row_get(row, "reservation_id"),
            "reservation_status": _row_get(row, "status"),
            "balance": _money(_row_get(row, "balance")),
            "room_number": _row_get(row, "room_number"),
            "check_out": _row_get(row, "check_out"),
            "href": "/app/hotel/desk",
        })

    # Arrivals not checked in (still reserved for today or earlier) — after no-show processing
    pending = conn.execute(
        """SELECT r.id AS reservation_id, r.status, r.check_in, r.check_out,
                  g.first_name, g.last_name, rm.room_number
           FROM hospitalityclaw_reservation r
           LEFT JOIN hospitalityclaw_guest g ON g.id = r.guest_id
           LEFT JOIN hospitalityclaw_room rm ON rm.id = r.room_id
           WHERE r.tenant_id = ? AND r.hotel_id = ?
             AND r.status IN ('reserved', 'pre_checked_in')
             AND r.check_in <= ?""",
        (tenant_id, hotel_id, business_date),
    ).fetchall()
    for row in pending:
        guest = f"{_row_get(row, 'first_name') or ''} {_row_get(row, 'last_name') or ''}".strip()
        exceptions.append({
            "code": "pending_arrival",
            "severity": "warning",
            "title": "Arrival not checked in",
            "message": (
                f"{guest or 'Guest'} expected {_row_get(row, 'check_in')} "
                f"(status {_row_get(row, 'status')})"
            ),
            "reservation_id": _row_get(row, "reservation_id"),
            "reservation_status": _row_get(row, "status"),
            "room_number": _row_get(row, "room_number"),
            "check_in": _row_get(row, "check_in"),
            "href": "/app/hotel/desk",
        })

    # Out-of-order rooms
    ooo = conn.execute(
        """SELECT id, room_number, floor, status, available_from
           FROM hospitalityclaw_room
           WHERE tenant_id = ? AND hotel_id = ? AND status = 'out_of_order'
           ORDER BY room_number""",
        (tenant_id, hotel_id),
    ).fetchall()
    for row in ooo:
        exceptions.append({
            "code": "ooo_room",
            "severity": "info",
            "title": "Out of order",
            "message": (
                f"Room {_row_get(row, 'room_number')} is OOO"
                + (
                    f" until {_row_get(row, 'available_from')}"
                    if _row_get(row, "available_from")
                    else ""
                )
            ),
            "room_id": _row_get(row, "id"),
            "room_number": _row_get(row, "room_number"),
            "href": "/app/hotel/rooms",
        })

    return exceptions


def _compute_kpis(conn, tenant_id: str, hotel_id: str, business_date: str, currency: str) -> dict:
    rooms_available = conn.execute(
        """SELECT COUNT(*) AS c FROM hospitalityclaw_room
           WHERE tenant_id = ? AND hotel_id = ? AND status != 'out_of_order'""",
        (tenant_id, hotel_id),
    ).fetchone()
    available = int(_row_get(rooms_available, "c") or 0)

    occupied = conn.execute(
        """SELECT COUNT(DISTINCT COALESCE(r.room_id, r.id)) AS c
           FROM hospitalityclaw_reservation r
           WHERE r.tenant_id = ? AND r.hotel_id = ?
             AND r.status IN ('checked_in', 'checked_out')
             AND r.check_in <= ? AND r.check_out > ?""",
        (tenant_id, hotel_id, business_date, business_date),
    ).fetchone()
    rooms_occupied = int(_row_get(occupied, "c") or 0)

    room_rev = conn.execute(
        """SELECT COALESCE(SUM(CAST(c.amount AS REAL)), 0) AS total
           FROM hospitalityclaw_folio_charge c
           JOIN hospitalityclaw_folio f ON f.id = c.folio_id
           JOIN hospitalityclaw_reservation r ON r.id = f.reservation_id
           WHERE c.tenant_id = ? AND r.hotel_id = ?
             AND c.charge_type = 'room' AND c.posting_date = ?""",
        (tenant_id, hotel_id, business_date),
    ).fetchone()
    room_revenue = _dec(_row_get(room_rev, "total"))

    tax_liab = conn.execute(
        """SELECT COALESCE(SUM(CAST(c.amount AS REAL)), 0) AS total
           FROM hospitalityclaw_folio_charge c
           JOIN hospitalityclaw_folio f ON f.id = c.folio_id
           JOIN hospitalityclaw_reservation r ON r.id = f.reservation_id
           WHERE c.tenant_id = ? AND r.hotel_id = ?
             AND c.charge_type = 'tax' AND c.posting_date = ?""",
        (tenant_id, hotel_id, business_date),
    ).fetchone()
    tax_liabilities = _dec(_row_get(tax_liab, "total"))

    outstanding = conn.execute(
        """SELECT COALESCE(SUM(CAST(f.balance AS REAL)), 0) AS total
           FROM hospitalityclaw_folio f
           JOIN hospitalityclaw_reservation r ON r.id = f.reservation_id
           WHERE f.tenant_id = ? AND r.hotel_id = ? AND f.status = 'open'""",
        (tenant_id, hotel_id),
    ).fetchone()
    outstanding_bal = _dec(_row_get(outstanding, "total"))

    occupancy_pct = (
        (Decimal(rooms_occupied) / Decimal(available) * Decimal("100")).quantize(Decimal("0.01"))
        if available > 0
        else Decimal("0")
    )
    revpar = (
        (room_revenue / Decimal(available)).quantize(Decimal("0.01"))
        if available > 0
        else Decimal("0")
    )

    return {
        "currency": currency,
        "rooms_available": available,
        "rooms_occupied": rooms_occupied,
        "occupancy_rate_pct": _money(occupancy_pct),
        "room_revenue": _money(room_revenue),
        "revpar": _money(revpar),
        "outstanding_folio_balances": _money(outstanding_bal),
        "tax_liabilities": _money(tax_liabilities),
    }


async def _erp_period_check(tenant_id: str) -> dict:
    try:
        from skills.executor import execute_skill
        result = await execute_skill(
            "erpclaw",
            "validate-period-close",
            {},
            tenant_id=tenant_id,
        )
        if isinstance(result, dict):
            return {
                "status": result.get("status") or "ok",
                "message": result.get("message"),
                "detail": {
                    k: result.get(k)
                    for k in ("can_close", "warnings", "errors", "fiscal_year")
                    if k in result
                },
            }
        return {"status": "ok", "message": str(result)}
    except Exception as exc:
        log.warning("ERP period close validation skipped: %s", exc)
        return {"status": "skipped", "message": str(exc)}


async def _email_report(summary: dict, recipients: list[dict]) -> list[dict]:
    if not recipients:
        return []
    from notifications import channels

    if not channels.email_configured():
        return [{"status": "skipped", "reason": "email not configured"}]

    pdf = build_night_audit_pdf(summary)
    json_bytes = json.dumps(summary, indent=2, default=str).encode("utf-8")
    hotel_name = (summary.get("hotel") or {}).get("name") or "Hotel"
    bdate = summary.get("business_date")
    subject = f"Night Audit Report — {hotel_name} — {bdate}"
    kpis = summary.get("kpis") or {}
    body = (
        f"Night audit completed for {hotel_name} on {bdate}.\n\n"
        f"Room revenue: {kpis.get('room_revenue')} {kpis.get('currency')}\n"
        f"Occupancy: {kpis.get('occupancy_rate_pct')}%\n"
        f"RevPAR: {kpis.get('revpar')}\n"
        f"Outstanding folios: {kpis.get('outstanding_folio_balances')}\n"
        f"Tax liabilities: {kpis.get('tax_liabilities')}\n"
        f"No-shows: {summary.get('no_shows_marked', 0)}\n\n"
        "PDF and JSON summaries are attached."
    )
    results = []
    for r in recipients:
        try:
            res = await channels.send_email(
                to=r["email"],
                subject=subject,
                body=body,
                attachments=[
                    {
                        "filename": f"night-audit-{bdate}.pdf",
                        "content": pdf,
                        "mime_type": "application/pdf",
                    },
                    {
                        "filename": f"night-audit-{bdate}.json",
                        "content": json_bytes,
                        "mime_type": "application/json",
                    },
                ],
            )
            results.append({"email": r["email"], "role": r.get("role_code"), **res})
        except Exception as exc:
            log.exception("night audit email failed to %s", r.get("email"))
            results.append({"email": r["email"], "status": "error", "message": str(exc)})
    return results


async def run_night_audit_for_hotel(
    *,
    tenant_id: str,
    hotel_id: str,
    business_date: str | None = None,
    triggered_by: str = "scheduler",
    send_email: bool = True,
    force: bool = False,
) -> dict:
    """Run full night audit for one hotel. Idempotent room posting."""
    conn = _erp()
    if not conn:
        return {"status": "error", "message": "Database unavailable"}

    hq = _hq()
    run_id = str(uuid.uuid4())
    started = datetime.now(timezone.utc).isoformat()

    try:
        _init_hosp(conn)
        hotel_row = conn.execute(
            """SELECT id, tenant_id, name, currency, timezone, settings_json
               FROM hospitalityclaw_hotel WHERE id = ? AND tenant_id = ?""",
            (hotel_id, tenant_id),
        ).fetchone()
        if not hotel_row:
            return {"status": "error", "message": "Hotel not found"}

        hotel = {
            "id": hotel_row["id"],
            "tenant_id": hotel_row["tenant_id"],
            "name": hotel_row["name"],
            "currency": hotel_row["currency"] or "USD",
            "timezone": hotel_row["timezone"] or "UTC",
            "settings_json": hotel_row["settings_json"],
        }
        settings = _parse_settings(hotel["settings_json"])
        policy = _night_audit_policy(settings)
        bdate = resolve_business_date(
            hotel["timezone"],
            explicit=business_date,
            for_scheduler=triggered_by == "scheduler",
            audit_hour=policy["audit_hour"],
        )

        if not force and already_ran(tenant_id, hotel_id, bdate) and triggered_by == "scheduler":
            return {
                "status": "ok",
                "skipped": True,
                "reason": "already_completed",
                "business_date": bdate,
                "hotel_id": hotel_id,
            }

        conn.execute(
            """INSERT INTO hospitalityclaw_night_audit_run
               (id, tenant_id, hotel_id, business_date, status, triggered_by, started_at)
               VALUES (?, ?, ?, ?, 'running', ?, ?)""",
            (run_id, tenant_id, hotel_id, bdate, triggered_by, started),
        )
        conn.commit()

        # 1) Room charges + taxes
        room = _post_room_charges(conn, hq, tenant_id, hotel_id, bdate)

        # 2) No-shows + penalties
        no_shows = _process_no_shows(conn, hq, tenant_id, hotel, bdate, policy)

        # 3) Cash drawer close
        drawer = _close_cash_drawer(
            conn, tenant_id, hotel_id, bdate, run_id=run_id, closed_by=triggered_by,
        )

        conn.commit()

        # 4) KPIs
        kpis = _compute_kpis(conn, tenant_id, hotel_id, bdate, hotel["currency"])

        # 5) Exception queue (open folios, overstays, OOO, pending arrivals)
        exceptions = collect_night_audit_exceptions(conn, tenant_id, hotel_id, bdate)

        # 6) ERP period soft-check (day ledger / fiscal readiness)
        erp_check = await _erp_period_check(tenant_id)

        blocking = [e for e in exceptions if e.get("severity") == "error"]
        run_status = "completed_with_exceptions" if exceptions else "completed"
        if policy.get("block_on_exceptions") and blocking:
            run_status = "completed_with_exceptions"

        summary = {
            "status": "ok",
            "run_id": run_id,
            "tenant_id": tenant_id,
            "hotel_id": hotel_id,
            "hotel": {"id": hotel_id, "name": hotel["name"], "currency": hotel["currency"]},
            "business_date": bdate,
            "triggered_by": triggered_by,
            "started_at": started,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "room_charges_posted": len(room["posted"]),
            "room_charges_skipped": len(room["skipped"]),
            "tax_lines_posted": room["tax_lines"],
            "gl_posts": room["gl_posts"],
            "room_postings": room["posted"],
            "no_shows_marked": no_shows["count"],
            "no_show_penalties_posted": no_shows["penalty_count"],
            "no_shows": no_shows["marked"],
            "no_show_penalties": no_shows["penalties"],
            "cash_drawer": drawer,
            "cash_drawer_status": drawer.get("status"),
            "erp_period": erp_check,
            "erp_period_status": erp_check.get("status"),
            "kpis": kpis,
            "exceptions": exceptions,
            "exception_count": len(exceptions),
            "exception_blocking_count": len(blocking),
            "currency": hotel["currency"],
            "run_status": run_status,
        }

        email_results = []
        if send_email and policy.get("email_report", True):
            recipients = _staff_finance_emails(tenant_id)
            email_results = await _email_report(summary, recipients)
        summary["email"] = email_results

        conn.execute(
            """UPDATE hospitalityclaw_night_audit_run
               SET status = ?, summary_json = ?, completed_at = ?
               WHERE id = ?""",
            (run_status, json.dumps(summary, default=str), summary["completed_at"], run_id),
        )
        conn.commit()

        try:
            from events import emit_data_change
            await emit_data_change("hospitalityclaw", "reservation")
            await emit_data_change("hospitalityclaw", "folio")
        except Exception:
            pass

        return summary
    except Exception as exc:
        log.exception("night audit failed hotel=%s", hotel_id)
        try:
            conn.execute(
                """UPDATE hospitalityclaw_night_audit_run
                   SET status = 'failed', summary_json = ?, completed_at = ?
                   WHERE id = ?""",
                (
                    json.dumps({"error": str(exc)}),
                    datetime.now(timezone.utc).isoformat(),
                    run_id,
                ),
            )
            conn.commit()
        except Exception:
            pass
        return {"status": "error", "message": str(exc), "run_id": run_id, "hotel_id": hotel_id}
    finally:
        conn.close()


async def run_night_audit(
    *,
    tenant_id: str | None = None,
    hotel_id: str | None = None,
    business_date: str | None = None,
    triggered_by: str = "scheduler",
    send_email: bool = True,
    force: bool = False,
) -> dict:
    """Run night audit for one hotel or all hotels (optionally scoped by tenant)."""
    hotels = list_hotels_for_audit(tenant_id)
    if hotel_id:
        hotels = [h for h in hotels if h["id"] == hotel_id]
        if tenant_id:
            hotels = [h for h in hotels if h["tenant_id"] == tenant_id]

    if not hotels:
        return {"status": "error", "message": "No hotels found for night audit"}

    results = []
    for h in hotels:
        res = await run_night_audit_for_hotel(
            tenant_id=h["tenant_id"],
            hotel_id=h["id"],
            business_date=business_date,
            triggered_by=triggered_by,
            send_email=send_email,
            force=force,
        )
        results.append(res)

    return {
        "status": "ok",
        "hotels": len(results),
        "results": results,
    }


async def run_due_night_audits() -> dict:
    """Scheduler entry: for each hotel past audit_hour, close prior business day if needed."""
    hotels = list_hotels_for_audit()
    ran = []
    skipped = []
    for h in hotels:
        settings = _parse_settings(h.get("settings_json"))
        policy = _night_audit_policy(settings)
        now = hotel_local_now(h.get("timezone"))
        if now.hour < int(policy["audit_hour"]):
            skipped.append({"hotel_id": h["id"], "reason": "before_audit_hour"})
            continue
        bdate = resolve_business_date(
            h.get("timezone"),
            for_scheduler=True,
            audit_hour=policy["audit_hour"],
        )
        if already_ran(h["tenant_id"], h["id"], bdate):
            skipped.append({"hotel_id": h["id"], "reason": "already_completed", "business_date": bdate})
            continue
        res = await run_night_audit_for_hotel(
            tenant_id=h["tenant_id"],
            hotel_id=h["id"],
            business_date=bdate,
            triggered_by="scheduler",
            send_email=True,
            force=False,
        )
        ran.append(res)
    return {"status": "ok", "ran": ran, "skipped": skipped}
