"""Channel manager — outbound inventory push and inbound OTA bookings."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pgutil import connect_erp

from services import channel_manager as cm


def _connect(_ignored=None):
    return connect_erp()


@pytest.fixture
def hotel(setup_dbs, monkeypatch):
    tid = str(uuid.uuid4())
    hotel_id = str(uuid.uuid4())
    rt_id = str(uuid.uuid4())
    room_id = str(uuid.uuid4())
    today = date.today()

    conn = connect_erp()
    cm.ensure_schema(conn)
    abbr = tid.replace("-", "")[:8]
    conn.execute(
        "INSERT INTO company (id, name, abbr) VALUES (?, ?, ?)",
        (tid, f"Co-{abbr}", abbr),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_hotel
           (id, tenant_id, name, currency, timezone) VALUES (?, ?, 'Harbor Inn', 'USD', 'UTC')""",
        (hotel_id, tid),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_room_type
           (id, tenant_id, hotel_id, name, code, base_rate, max_occupancy)
           VALUES (?, ?, ?, 'Deluxe', 'DLX', '100.00', 2)""",
        (rt_id, tid, hotel_id),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_room
           (id, tenant_id, hotel_id, room_type_id, room_number, status)
           VALUES (?, ?, ?, ?, '101', 'vacant')""",
        (room_id, tid, hotel_id, rt_id),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_tax_rule
           (id, tenant_id, hotel_id, name, rate_percent, applies_to, is_active, is_default)
           VALUES (?, ?, ?, 'Occupancy Tax', '10', 'room', 1, 1)""",
        (str(uuid.uuid4()), tid, hotel_id),
    )
    conn.commit()
    conn.close()

    return {
        "erp": None,
        "tenant_id": tid,
        "hotel_id": hotel_id,
        "room_type_id": rt_id,
        "room_id": room_id,
        "today": today,
    }


def _save_link(hotel, *, ota="booking_com", secret="whsec_test"):
    return cm.save_connection(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "ota": "Booking.com" if ota == "booking_com" else ota,
        "provider": "channex",
        "property_external_id": "PROP-1",
        "api_base_url": "https://channel.test/api/v1",
        "api_key": "ck_live_secret",
        "webhook_secret": secret,
        "status": "connected",
    })


def _signed(body: dict, secret="whsec_test"):
    raw = json.dumps(body).encode()
    return raw, {"X-OTA-Signature": cm.webhook_signature(secret, raw)}


def test_outbound_pushes_rates_and_drops_availability_after_booking(hotel):
    _save_link(hotel)
    cm.save_mapping(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "ota": "booking_com",
        "ota_room_type_code": "BDC-DLX",
        "room_type_id": hotel["room_type_id"],
    })
    tomorrow = (hotel["today"] + timedelta(days=1)).isoformat()
    later = (hotel["today"] + timedelta(days=2)).isoformat()
    conn = _connect(hotel["erp"])
    conn.execute(
        """INSERT INTO hospitalityclaw_rate_plan
           (id, tenant_id, hotel_id, room_type_id, name, code, start_date, end_date,
            nightly_rate, priority, is_active)
           VALUES (?, ?, ?, ?, 'Peak', 'PEAK', ?, ?, '180.00', 1, 1)""",
        (str(uuid.uuid4()), hotel["tenant_id"], hotel["hotel_id"], hotel["room_type_id"],
         hotel["today"].isoformat(), later),
    )
    guest_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_guest (id, tenant_id, first_name, last_name)
           VALUES (?, ?, 'Front', 'Desk')""",
        (guest_id, hotel["tenant_id"]),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_reservation
           (id, tenant_id, hotel_id, guest_id, room_id, room_type_id,
            check_in, check_out, adults, status, rate)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'reserved', '180.00')""",
        (str(uuid.uuid4()), hotel["tenant_id"], hotel["hotel_id"], guest_id,
         hotel["room_id"], hotel["room_type_id"], tomorrow, later),
    )
    conn.commit()
    conn.close()

    captured = []

    def transport(url, payload, headers):
        captured.append((url, payload, headers))
        return 200, "ok"

    result = cm.push_outbound(
        hotel["tenant_id"], hotel_id=hotel["hotel_id"], reason="add-reservation",
        horizon_days=2, transport=transport,
    )
    assert result["status"] == "ok", result
    assert result["pushed"][0]["ota_label"] == "Booking.com"
    assert captured[0][2]["user-api-key"] == "ck_live_secret"
    availability = {row["date"]: row["availability"] for row in captured[0][1]["values"]}
    rates = {row["date"]: row for row in captured[1][1]["values"]}
    assert availability[hotel["today"].isoformat()] == 1
    assert availability[tomorrow] == 0
    assert rates[tomorrow]["rate"] == 18000
    assert rates[tomorrow]["rate_decimal"] == "180.00"
    assert rates[tomorrow]["room_type_id"] == "BDC-DLX"

    snap = cm.dashboard(hotel["tenant_id"])
    assert snap["connections"][0]["last_success_at"]
    assert snap["connections"][0]["api_key_configured"] is True
    assert "api_key" not in snap["connections"][0]
    assert snap["mappings"][0]["room_numbers"] == ["101"]


def test_unassigned_front_desk_booking_reduces_availability(hotel):
    _save_link(hotel)
    cm.save_mapping(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "ota": "booking_com",
        "ota_room_type_code": "BDC-DLX",
        "room_type_id": hotel["room_type_id"],
    })
    start = hotel["today"].isoformat()
    end = (hotel["today"] + timedelta(days=1)).isoformat()
    conn = _connect(hotel["erp"])
    guest_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO hospitalityclaw_guest (id, tenant_id, first_name, last_name) VALUES (?, ?, 'A', 'B')",
        (guest_id, hotel["tenant_id"]),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_reservation
           (id, tenant_id, hotel_id, guest_id, room_type_id, check_in, check_out, status, rate)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', '100.00')""",
        (str(uuid.uuid4()), hotel["tenant_id"], hotel["hotel_id"], guest_id,
         hotel["room_type_id"], start, end),
    )
    conn.commit()
    conn.close()

    captured = []
    cm.push_outbound(
        hotel["tenant_id"], horizon_days=1,
        transport=lambda url, payload, headers: captured.append(payload) or (200, "ok"),
    )
    assert captured[0]["values"][0]["availability"] == 0


def test_maintenance_room_is_not_sold_to_ota(hotel):
    _save_link(hotel)
    cm.save_mapping(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "ota": "expedia",
        "ota_room_type_code": "EXP-1",
        "room_type_id": hotel["room_type_id"],
    })
    conn = _connect(hotel["erp"])
    conn.execute(
        "UPDATE hospitalityclaw_room SET status = 'out_of_order' WHERE id = ?",
        (hotel["room_id"],),
    )
    conn.execute(
        "UPDATE hospitalityclaw_channel_connection SET provider = 'siteminder', ota = 'expedia' WHERE tenant_id = ?",
        (hotel["tenant_id"],),
    )
    conn.commit()
    conn.close()
    captured = []
    cm.push_outbound(
        hotel["tenant_id"], horizon_days=1,
        transport=lambda url, payload, headers: captured.append((url, payload, headers)) or (200, "ok"),
    )
    assert captured[0][0].endswith("/availability")
    assert captured[0][1]["availStatusMessages"][0]["bookingLimit"] == 0
    assert captured[0][2]["Authorization"].startswith("Bearer ")
    assert captured[1][0].endswith("/rates")


def test_missing_mapping_is_logged_and_not_pushed(hotel):
    _save_link(hotel)
    captured = []
    result = cm.push_outbound(
        hotel["tenant_id"], horizon_days=1,
        transport=lambda url, payload, headers: captured.append(url) or (200, "ok"),
    )
    assert captured == []
    assert result["errors"]
    snap = cm.dashboard(hotel["tenant_id"])
    assert snap["errors"]
    assert "mapping" in snap["errors"][0]["message"].lower() or "DLX" in snap["errors"][0]["message"]


def test_inbound_booking_allocates_room_and_posts_source(hotel):
    _save_link(hotel)
    cm.save_mapping(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "ota": "booking_com",
        "ota_room_type_code": "BDC-DLX",
        "room_type_id": hotel["room_type_id"],
    })
    check_in = (hotel["today"] + timedelta(days=10)).isoformat()
    check_out = (hotel["today"] + timedelta(days=12)).isoformat()
    raw, headers = _signed({
        "event": "booking",
        "payload": {
            "booking_id": "BDC-99",
            "property_id": "PROP-1",
            "ota_name": "Booking.com",
            "status": "new",
            "customer": {"name": "Ada", "surname": "Lovelace", "mail": "ada@example.com"},
            "rooms": [{
                "room_type_id": "BDC-DLX",
                "checkin_date": check_in,
                "checkout_date": check_out,
                "days": {check_in: "150.00", (hotel["today"] + timedelta(days=11)).isoformat(): "160.00"},
                "occupancy": {"adults": 2, "children": 0},
            }],
        },
    })
    result = cm.handle_webhook(raw, headers)
    assert result["status"] == "ok", result
    assert result["source"] == "Booking.com"
    assert result["room_numbers"] == ["101"]

    conn = _connect(hotel["erp"])
    res = conn.execute(
        "SELECT * FROM hospitalityclaw_reservation WHERE id = ?",
        (result["reservation_id"],),
    ).fetchone()
    assert res["room_id"] == hotel["room_id"]
    assert res["status"] == "reserved"
    charges = conn.execute(
        """SELECT description, amount, charge_type FROM hospitalityclaw_folio_charge
           WHERE folio_id = ? ORDER BY posting_date""",
        (result["folio_id"],),
    ).fetchall()
    room_charges = [row for row in charges if row["charge_type"] == "room"]
    assert [row["amount"] for row in room_charges] == ["150.00", "160.00"]
    sources = conn.execute(
        """SELECT DISTINCT source FROM hospitalityclaw_ledger_entry
           WHERE folio_id = ? AND debit != '0'""",
        (result["folio_id"],),
    ).fetchall()
    assert {row["source"] for row in sources} == {"Booking.com"}
    conn.close()

    again = cm.handle_webhook(raw, headers)
    assert again["duplicate"] is True
    conn = _connect(hotel["erp"])
    count = conn.execute("SELECT COUNT(*) FROM hospitalityclaw_reservation").fetchone()[0]
    assert count == 1
    conn.close()


def test_airbnb_webhook_and_bad_signature(hotel):
    cm.save_connection(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "ota": "Airbnb",
        "provider": "channex",
        "property_external_id": hotel["hotel_id"],
        "api_key": "key",
        "webhook_secret": "airbnb-secret",
        "status": "connected",
    })
    cm.save_mapping(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "ota": "airbnb",
        "ota_room_type_code": "AB-1",
        "room_type_id": hotel["room_type_id"],
    })
    check_in = (hotel["today"] + timedelta(days=3)).isoformat()
    check_out = (hotel["today"] + timedelta(days=4)).isoformat()
    body = {
        "ota": "Airbnb",
        "property_id": hotel["hotel_id"],
        "booking_id": "AB-7",
        "status": "new",
        "guest": {"first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com"},
        "room_type_code": "AB-1",
        "check_in": check_in,
        "check_out": check_out,
        "nightly_rate": "90.00",
    }
    raw = json.dumps(body).encode()
    rejected = cm.handle_webhook(raw, {"X-OTA-Signature": "sha256=deadbeef"})
    assert rejected["http_status"] == 401

    accepted = cm.handle_webhook(raw, {"X-OTA-Signature": cm.webhook_signature("airbnb-secret", raw)})
    assert accepted["http_status"] == 200
    assert accepted["source"] == "Airbnb"
    conn = _connect(hotel["erp"])
    source = conn.execute(
        "SELECT source FROM hospitalityclaw_ledger_entry WHERE folio_id = ? AND account_code = 'ROOM_REV'",
        (accepted["folio_id"],),
    ).fetchone()
    assert source["source"] == "Airbnb"
    conn.close()


def test_unknown_room_type_does_not_create_a_reservation(hotel):
    _save_link(hotel)
    check_in = (hotel["today"] + timedelta(days=5)).isoformat()
    check_out = (hotel["today"] + timedelta(days=6)).isoformat()
    raw, headers = _signed({
        "ota": "Booking.com",
        "property_id": "PROP-1",
        "booking_id": "BDC-missing",
        "guest": {"first_name": "No", "last_name": "Map"},
        "room_type_code": "UNKNOWN-SUITE",
        "check_in": check_in,
        "check_out": check_out,
        "nightly_rate": "200.00",
    })
    result = cm.handle_webhook(raw, headers)
    assert result["http_status"] == 422
    assert "mapping" in result["message"].lower()
    conn = _connect(hotel["erp"])
    assert conn.execute("SELECT COUNT(*) FROM hospitalityclaw_reservation").fetchone()[0] == 0
    errors = conn.execute(
        "SELECT message FROM hospitalityclaw_channel_sync_log WHERE status = 'error'"
    ).fetchall()
    assert errors
    conn.close()


def test_sync_hook_ignores_unrelated_actions():
    result = asyncio.run(cm.sync_after_skill_action(
        skill="hospitalityclaw", action="list-rooms", tenant_id="t",
    ))
    assert result is None


def test_sync_hook_pushes_after_rate_plan_update(hotel, monkeypatch):
    calls = []

    def fake(tenant_id, **kwargs):
        calls.append(kwargs)
        return {"status": "ok", "pushed": []}

    monkeypatch.setattr(cm, "push_outbound", fake)
    asyncio.run(cm.sync_after_skill_action(
        skill="hospitalityclaw",
        action="update-rate-plan",
        tenant_id=hotel["tenant_id"],
        params={"hotel_id": hotel["hotel_id"]},
        result={"status": "ok"},
    ))
    assert calls[0]["reason"] == "update-rate-plan"
    assert calls[0]["hotel_id"] == hotel["hotel_id"]


def test_agoda_connection_and_cancel_modify(hotel):
    assert "agoda" in cm.OTAS
    saved = cm.save_connection(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "ota": "Agoda",
        "provider": "channex",
        "property_external_id": "AG-PROP",
        "api_key": "agoda-key",
        "webhook_secret": "agoda-secret",
        "status": "connected",
    })
    assert saved["ota"] == "agoda"
    assert saved["ota_label"] == "Agoda"
    cm.save_mapping(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "ota": "agoda",
        "ota_room_type_code": "AG-DLX",
        "room_type_id": hotel["room_type_id"],
    })

    check_in = (hotel["today"] + timedelta(days=8)).isoformat()
    check_out = (hotel["today"] + timedelta(days=10)).isoformat()
    body = {
        "ota": "Agoda",
        "property_id": "AG-PROP",
        "booking_id": "AG-100",
        "status": "new",
        "guest": {"first_name": "Kim", "last_name": "Lee", "email": "kim@example.com"},
        "room_type_code": "AG-DLX",
        "check_in": check_in,
        "check_out": check_out,
        "nightly_rate": "120.00",
    }
    raw = json.dumps(body).encode()
    created = cm.handle_webhook(raw, {"X-OTA-Signature": cm.webhook_signature("agoda-secret", raw)})
    assert created["status"] == "ok", created
    assert created["source"] == "Agoda"
    reservation_id = created["reservation_id"]

    amended_out = (hotel["today"] + timedelta(days=11)).isoformat()
    body["check_out"] = amended_out
    body["nightly_rate"] = "130.00"
    raw2 = json.dumps(body).encode()
    amended = cm.handle_webhook(raw2, {"X-OTA-Signature": cm.webhook_signature("agoda-secret", raw2)})
    assert amended.get("modified") is True, amended
    conn = _connect(hotel["erp"])
    res = conn.execute(
        "SELECT check_out, rate FROM hospitalityclaw_reservation WHERE id = ?",
        (reservation_id,),
    ).fetchone()
    assert res["check_out"] == amended_out
    assert res["rate"] == "130.00"
    conn.close()

    cancel_body = {**body, "status": "cancelled"}
    raw3 = json.dumps(cancel_body).encode()
    cancelled = cm.handle_webhook(raw3, {"X-OTA-Signature": cm.webhook_signature("agoda-secret", raw3)})
    assert cancelled.get("cancelled") is True
    conn = _connect(hotel["erp"])
    status = conn.execute(
        "SELECT status FROM hospitalityclaw_reservation WHERE id = ?",
        (reservation_id,),
    ).fetchone()["status"]
    assert status == "cancelled"
    conn.close()


def test_http_retry_on_transient_failure(hotel):
    _save_link(hotel)
    cm.save_mapping(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "ota": "booking_com",
        "ota_room_type_code": "BDC-DLX",
        "room_type_id": hotel["room_type_id"],
    })
    attempts = {"n": 0}

    def flaky(url, payload, headers):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            return 503, "busy"
        return 200, "ok"

    result = cm.push_outbound(
        hotel["tenant_id"],
        hotel_id=hotel["hotel_id"],
        ota="booking_com",
        reason="retry-test",
        horizon_days=1,
        transport=flaky,
    )
    assert result["status"] == "ok", result
    assert attempts["n"] >= 3
