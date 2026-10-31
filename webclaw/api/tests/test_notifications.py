"""Tests for guest notification engine — templates, PDF, dispatch, pre-arrival."""
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

from notifications.defaults import merge_notification_settings
from notifications.templates import render_template
from notifications.pdf_receipt import build_folio_pdf
from notifications import engine as notif_engine
from notifications.scheduler import tomorrow_iso
from notifications.store import (
    ensure_notification_tables,
    load_hotel_notification_settings,
    save_hotel_notification_settings,
)



def test_render_template_placeholders():
    out = render_template(
        "Hi {{guest_name}} — check-in {{checkin_date}} ({{missing}})",
        {"guest_name": "Ada", "checkin_date": "2026-10-01"},
    )
    assert out == "Hi Ada — check-in 2026-10-01 ()"


def test_merge_notification_settings_defaults():
    merged = merge_notification_settings(
        {"booking_confirmation": {"enabled": False, "channels": {"sms": False}}}
    )
    assert merged["booking_confirmation"]["enabled"] is False
    assert merged["booking_confirmation"]["channels"]["email"] is True
    assert merged["booking_confirmation"]["channels"]["sms"] is False
    assert merged["pre_arrival"]["enabled"] is True
    assert "{{guest_name}}" in merged["pre_arrival"]["email_body"]


def test_build_folio_pdf_is_valid_pdf():
    pdf = build_folio_pdf(
        hotel_name="Harbor Inn",
        guest_name="Ada Lovelace",
        folio_id="folio-1",
        confirmation_code="ABC123",
        checkin_date="2026-10-01",
        checkout_date="2026-10-03",
        currency="USD",
        charges=[
            {"description": "Room", "amount": "240", "charge_type": "room", "posting_date": "2026-10-01"},
            {"description": "Payment", "amount": "240", "charge_type": "payment", "posting_date": "2026-10-03"},
        ],
        total_amount="240.00",
        balance="0.00",
    )
    assert pdf.startswith(b"%PDF-1.4")
    assert b"%%EOF" in pdf
    assert b"Harbor Inn" in pdf


def test_load_and_save_hotel_settings(setup_dbs):
    tid = setup_dbs["tenant_id"]
    settings = load_hotel_notification_settings(tid)
    assert settings["booking_confirmation"]["enabled"] is True
    settings["pre_arrival"]["enabled"] = False
    settings["pre_arrival"]["email_subject"] = "Custom subject {{hotel_name}}"
    saved = save_hotel_notification_settings(tid, settings)
    assert saved["pre_arrival"]["enabled"] is False
    assert "Custom subject" in saved["pre_arrival"]["email_subject"]
    reloaded = load_hotel_notification_settings(tid)
    assert reloaded["pre_arrival"]["enabled"] is False


def test_dispatch_booking_confirmation(setup_dbs):
    tid = setup_dbs["tenant_id"]
    rid = setup_dbs["reservation_id"]

    async def _run():
        with (
            patch(
                "notifications.channels.send_email",
                new_callable=AsyncMock,
                return_value={"ok": True, "provider": "sendgrid"},
            ) as email,
            patch(
                "notifications.channels.send_sms",
                new_callable=AsyncMock,
                return_value={"ok": True, "provider": "twilio", "sid": "SM1"},
            ) as sms,
        ):
            result = await notif_engine.dispatch_trigger(
                tenant_id=tid,
                trigger_key="booking_confirmation",
                reservation_id=rid,
            )
            assert result["status"] == "ok"
            email.assert_awaited()
            sms.assert_awaited()
            deliveries = {d["channel"]: d for d in result["deliveries"]}
            assert deliveries["email"]["status"] == "sent"
            assert deliveries["sms"]["status"] == "sent"

            again = await notif_engine.dispatch_trigger(
                tenant_id=tid,
                trigger_key="booking_confirmation",
                reservation_id=rid,
            )
            assert all(d["status"] == "skipped" for d in again["deliveries"])

    asyncio.run(_run())


def test_dispatch_folio_receipt_attaches_pdf(setup_dbs):
    tid = setup_dbs["tenant_id"]
    rid = setup_dbs["reservation_id"]
    folio_id = setup_dbs["folio_id"]

    async def _run():
        with patch(
            "notifications.channels.send_email",
            new_callable=AsyncMock,
            return_value={"ok": True, "provider": "sendgrid"},
        ) as email:
            result = await notif_engine.dispatch_trigger(
                tenant_id=tid,
                trigger_key="folio_receipt",
                reservation_id=rid,
                folio_id=folio_id,
            )
            assert result["status"] == "ok"
            email.assert_awaited()
            kwargs = email.await_args.kwargs
            assert kwargs["attachments"]
            assert kwargs["attachments"][0]["content"].startswith(b"%PDF")

    asyncio.run(_run())


def test_skill_action_mapping(setup_dbs):
    tid = setup_dbs["tenant_id"]
    rid = setup_dbs["reservation_id"]

    async def _run():
        with patch.object(
            notif_engine,
            "dispatch_trigger",
            new_callable=AsyncMock,
            return_value={"status": "ok"},
        ) as disp:
            await notif_engine.dispatch_for_skill_action(
                skill="hospitalityclaw",
                action="add-reservation",
                tenant_id=tid,
                result={"reservation_id": rid, "folio_id": setup_dbs["folio_id"]},
                params={},
            )
            disp.assert_awaited()
            assert disp.await_args.kwargs["trigger_key"] == "booking_confirmation"

    asyncio.run(_run())


def test_tomorrow_iso():
    assert tomorrow_iso() == (date.today() + timedelta(days=1)).isoformat()


def test_ensure_log_table(setup_dbs):
    conn = ensure_notification_tables()
    row = conn.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'webclaw'
          AND table_name = 'webclaw_notification_log'
        """
    ).fetchone()
    assert row is not None
