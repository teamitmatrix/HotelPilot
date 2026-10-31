"""Night audit engine — room posting, no-shows, cash drawer, report PDF."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pgutil import connect_web, connect_erp



def test_build_night_audit_pdf():
    from jobs.night_audit_report import build_night_audit_pdf

    pdf = build_night_audit_pdf(
        {
            "business_date": "2026-10-01",
            "run_id": "abc",
            "triggered_by": "test",
            "hotel": {"name": "Harbor Inn", "currency": "USD"},
            "kpis": {
                "room_revenue": "100.00",
                "tax_liabilities": "10.00",
                "outstanding_folio_balances": "110.00",
                "rooms_occupied": 1,
                "rooms_available": 10,
                "occupancy_rate_pct": "10.00",
                "revpar": "10.00",
            },
            "room_charges_posted": 1,
            "room_charges_skipped": 0,
            "tax_lines_posted": 1,
            "no_shows_marked": 1,
            "no_show_penalties_posted": 1,
            "cash_drawer_status": "closed",
            "erp_period_status": "ok",
        }
    )
    assert pdf.startswith(b"%PDF")
    assert b"Night Audit" in pdf or b"Harbor" in pdf


def test_night_audit_posts_room_tax_no_show_and_drawer(setup_audit, monkeypatch):
    from jobs import night_audit as na

    monkeypatch.setattr(na, "_erp_period_check", AsyncMock(return_value={"status": "ok"}))
    monkeypatch.setattr(na, "_email_report", AsyncMock(return_value=[{"status": "ok"}]))

    result = asyncio.run(
        na.run_night_audit_for_hotel(
            tenant_id=setup_audit["tenant_id"],
            hotel_id=setup_audit["hotel_id"],
            business_date=setup_audit["today"],
            triggered_by="test",
            send_email=True,
            force=True,
        )
    )
    assert result["status"] == "ok", result
    assert result["room_charges_posted"] >= 1
    assert result["tax_lines_posted"] >= 1
    assert result["no_shows_marked"] >= 1
    assert result["no_show_penalties_posted"] >= 1
    assert result["cash_drawer"]["status"] == "closed"
    assert float(result["cash_drawer"]["cash_received"]) == 50.0
    assert result["kpis"]["room_revenue"]
    assert "occupancy_rate_pct" in result["kpis"]
    assert "revpar" in result["kpis"]

    erp = connect_erp()
    status = erp.execute(
        "SELECT status FROM hospitalityclaw_reservation WHERE id = ?",
        (setup_audit["rid_ns"],),
    ).fetchone()["status"]
    assert status == "no_show"

    room_charge = erp.execute(
        """SELECT COUNT(*) AS c FROM hospitalityclaw_folio_charge
           WHERE folio_id = ? AND charge_type = 'room' AND posting_date = ?""",
        (setup_audit["folio_in"], setup_audit["today"]),
    ).fetchone()["c"]
    assert room_charge == 1

    drawer = erp.execute(
        """SELECT status FROM hospitalityclaw_cash_drawer_day
           WHERE hotel_id = ? AND business_date = ?""",
        (setup_audit["hotel_id"], setup_audit["today"]),
    ).fetchone()
    assert drawer["status"] == "closed"
    erp.close()


def test_night_audit_room_posting_idempotent(setup_audit, monkeypatch):
    from jobs import night_audit as na

    monkeypatch.setattr(na, "_erp_period_check", AsyncMock(return_value={"status": "ok"}))
    monkeypatch.setattr(na, "_email_report", AsyncMock(return_value=[]))

    r1 = asyncio.run(
        na.run_night_audit_for_hotel(
            tenant_id=setup_audit["tenant_id"],
            hotel_id=setup_audit["hotel_id"],
            business_date=setup_audit["today"],
            triggered_by="test",
            send_email=False,
            force=True,
        )
    )
    r2 = asyncio.run(
        na.run_night_audit_for_hotel(
            tenant_id=setup_audit["tenant_id"],
            hotel_id=setup_audit["hotel_id"],
            business_date=setup_audit["today"],
            triggered_by="test",
            send_email=False,
            force=True,
        )
    )
    assert r1["room_charges_posted"] >= 1
    assert r2["room_charges_skipped"] >= 1
    assert r2["room_charges_posted"] == 0


def test_staff_finance_emails(setup_audit):
    from jobs.night_audit import _staff_finance_emails

    emails = _staff_finance_emails(setup_audit["tenant_id"])
    addrs = {e["email"] for e in emails}
    assert "owner@hotel.test" in addrs
    assert "books@hotel.test" in addrs
