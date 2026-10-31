"""Build guest notification template context from hospitality data."""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import db


def _row_get(row, key: str, idx: int | None = None):
    if row is None:
        return None
    if hasattr(row, "keys"):
        try:
            return row[key]
        except Exception:
            return None
    if idx is not None:
        try:
            return row[idx]
        except Exception:
            return None
    return None


def _parse_settings(raw) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _nights(check_in: str | None, check_out: str | None) -> int:
    try:
        if not check_in or not check_out:
            return 0
        d0 = date.fromisoformat(str(check_in)[:10])
        d1 = date.fromisoformat(str(check_out)[:10])
        return max(0, (d1 - d0).days)
    except Exception:
        return 0


def _money(val) -> str:
    try:
        return f"{Decimal(str(val or '0')):.2f}"
    except Exception:
        return str(val or "0")


def load_reservation_context(
    tenant_id: str,
    *,
    reservation_id: str | None = None,
    folio_id: str | None = None,
) -> dict[str, Any] | None:
    """Return placeholder context for a reservation / folio, or None."""
    skill_db = db.get_skill_db("erpclaw") or db.get_skill_db("hospitalityclaw")
    if not skill_db:
        return None
    try:
        if not reservation_id and folio_id:
            fro = skill_db.execute(
                "SELECT reservation_id FROM hospitalityclaw_folio WHERE id = ? AND tenant_id = ?",
                (folio_id, tenant_id),
            ).fetchone()
            if fro:
                reservation_id = _row_get(fro, "reservation_id", 0)

        if not reservation_id:
            return None

        row = skill_db.execute(
            """SELECT r.id, r.check_in, r.check_out, r.rate, r.status, r.hotel_id,
                      g.first_name, g.last_name, g.email AS guest_email, g.phone AS guest_phone,
                      rt.name AS room_type_name, rm.room_number,
                      h.name AS hotel_name, h.currency, h.address AS hotel_address,
                      h.phone AS hotel_phone, h.email AS hotel_email, h.settings_json,
                      f.id AS folio_id, f.balance AS folio_balance, f.status AS folio_status
               FROM hospitalityclaw_reservation r
               JOIN hospitalityclaw_guest g ON g.id = r.guest_id
               JOIN hospitalityclaw_room_type rt ON rt.id = r.room_type_id
               JOIN hospitalityclaw_hotel h ON h.id = r.hotel_id
               LEFT JOIN hospitalityclaw_room rm ON rm.id = r.room_id
               LEFT JOIN hospitalityclaw_folio f ON f.reservation_id = r.id AND f.tenant_id = r.tenant_id
               WHERE r.id = ? AND r.tenant_id = ?""",
            (reservation_id, tenant_id),
        ).fetchone()
        if not row:
            return None

        settings = _parse_settings(_row_get(row, "settings_json"))
        first = str(_row_get(row, "first_name") or "")
        last = str(_row_get(row, "last_name") or "")
        guest_name = f"{first} {last}".strip() or "Guest"
        check_in = str(_row_get(row, "check_in") or "")[:10]
        check_out = str(_row_get(row, "check_out") or "")[:10]
        folio = _row_get(row, "folio_id") or folio_id
        currency = str(_row_get(row, "currency") or "USD")
        rate = _money(_row_get(row, "rate"))
        nights = _nights(check_in, check_out)

        charges_summary = ""
        total_charges = Decimal("0")
        total_payments = Decimal("0")
        if folio:
            charges = skill_db.execute(
                """SELECT description, amount, charge_type, posting_date
                   FROM hospitalityclaw_folio_charge
                   WHERE tenant_id = ? AND folio_id = ?
                   ORDER BY posting_date, created_at""",
                (tenant_id, folio),
            ).fetchall()
            lines = []
            for c in charges:
                amt = Decimal(str(_row_get(c, "amount") or "0"))
                ctype = str(_row_get(c, "charge_type") or "other")
                desc = str(_row_get(c, "description") or ctype)
                pdate = str(_row_get(c, "posting_date") or "")[:10]
                sign = "-" if ctype == "payment" else ""
                if ctype == "payment":
                    total_payments += abs(amt)
                else:
                    total_charges += amt
                lines.append(f"{pdate}  {desc}: {sign}{_money(amt)} {currency}")
            charges_summary = "\n".join(lines) if lines else "(no charges)"

        # Prefer payments total for receipt; else sum of non-payment charges
        total_amount = _money(total_payments if total_payments else total_charges)
        if total_payments == 0 and total_charges == 0:
            try:
                total_amount = _money(Decimal(rate) * Decimal(nights or 1))
            except Exception:
                total_amount = rate

        conf = str(reservation_id).replace("-", "")[:8].upper()
        origin = os.environ.get("WEBCLAW_CORS_ORIGIN", "http://localhost:3000").rstrip("/")
        booking_token = ""
        try:
            tok_row = skill_db.execute(
                "SELECT booking_token FROM hospitalityclaw_reservation WHERE id = ?",
                (reservation_id,),
            ).fetchone()
            if tok_row:
                booking_token = str(_row_get(tok_row, "booking_token", 0) or "")
        except Exception:
            booking_token = ""
        portal_url = f"{origin}/guest/{booking_token}" if booking_token else ""
        return {
            "guest_name": guest_name,
            "guest_first_name": first or guest_name,
            "guest_email": str(_row_get(row, "guest_email") or ""),
            "guest_phone": str(_row_get(row, "guest_phone") or ""),
            "hotel_name": str(_row_get(row, "hotel_name") or "Hotel"),
            "hotel_address": str(_row_get(row, "hotel_address") or ""),
            "hotel_phone": str(_row_get(row, "hotel_phone") or ""),
            "hotel_email": str(_row_get(row, "hotel_email") or ""),
            "confirmation_code": conf,
            "reservation_id": reservation_id,
            "folio_id": str(folio or ""),
            "booking_token": booking_token,
            "portal_url": portal_url,
            "checkin_date": check_in,
            "checkout_date": check_out,
            "room_type": str(_row_get(row, "room_type_name") or ""),
            "room_number": str(_row_get(row, "room_number") or "TBA"),
            "nights": str(nights),
            "rate": rate,
            "currency": currency,
            "total_amount": total_amount,
            "balance": _money(_row_get(row, "folio_balance")),
            "check_in_time": str(settings.get("check_in_time") or "15:00"),
            "check_out_time": str(settings.get("check_out_time") or "11:00"),
            "cancellation_policy": str(settings.get("cancellation_policy") or "See hotel policy."),
            "charges_summary": charges_summary,
            "payment_method": "",
            "reservation_status": str(_row_get(row, "status") or ""),
            "folio_status": str(_row_get(row, "folio_status") or ""),
        }
    finally:
        skill_db.close()


def load_folio_charge_rows(tenant_id: str, folio_id: str) -> list[dict]:
    skill_db = db.get_skill_db("erpclaw") or db.get_skill_db("hospitalityclaw")
    if not skill_db:
        return []
    try:
        rows = skill_db.execute(
            """SELECT description, amount, charge_type, posting_date
               FROM hospitalityclaw_folio_charge
               WHERE tenant_id = ? AND folio_id = ?
               ORDER BY posting_date, created_at""",
            (tenant_id, folio_id),
        ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "description": _row_get(r, "description"),
                    "amount": _row_get(r, "amount"),
                    "charge_type": _row_get(r, "charge_type"),
                    "posting_date": _row_get(r, "posting_date"),
                }
            )
        return out
    finally:
        skill_db.close()


def find_pre_arrival_candidates(check_in_date: str) -> list[dict]:
    """Reservations checking in on ``check_in_date`` still in reserved status."""
    skill_db = db.get_skill_db("erpclaw") or db.get_skill_db("hospitalityclaw")
    if not skill_db:
        return []
    try:
        rows = skill_db.execute(
            """SELECT r.id AS reservation_id, r.tenant_id, r.check_in
               FROM hospitalityclaw_reservation r
               WHERE r.status = 'reserved' AND r.check_in = ?""",
            (check_in_date,),
        ).fetchall()
        return [
            {
                "reservation_id": _row_get(r, "reservation_id", 0),
                "tenant_id": _row_get(r, "tenant_id", 1),
                "check_in": _row_get(r, "check_in", 2),
            }
            for r in rows
        ]
    except Exception:
        return []
    finally:
        skill_db.close()
