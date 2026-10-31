"""Guest self-service portal — token auth, pre-register, checkout request."""
from __future__ import annotations

import base64
import io
import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pgutil import connect_web, connect_erp


TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)



@pytest.fixture
def client(setup_guest):
    from main import app

    return TestClient(app)


def test_guest_stay_requires_valid_token(client):
    r = client.get("/api/v1/guest/not-a-valid-token!!")
    assert r.status_code == 404


def test_guest_stay_summary(client, setup_guest):
    token = setup_guest["token"]
    r = client.get(f"/api/v1/guest/{token}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    stay = body["stay"]
    assert stay["confirmation_code"]
    assert stay["status"] == "reserved"
    assert stay["pre_checked_in"] is False
    assert stay["can_pre_register"] is True
    assert stay["can_pay"] is True
    assert stay["folio"]["balance"] == "150.00" or float(stay["folio"]["balance"]) == 150.0
    assert len(stay["folio"]["charges"]) >= 1
    assert stay["hotel"]["name"] == "Harbor Inn"
    assert stay["guest"]["first_name"] == "Ada"


def test_guest_pre_register_sets_pre_checked_in(client, setup_guest):
    token = setup_guest["token"]
    sig = "data:image/png;base64," + base64.b64encode(TINY_PNG).decode()
    r = client.post(
        f"/api/v1/guest/{token}/pre-register",
        json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "nationality": "GB",
            "id_number": "P123",
            "signature_data_url": sig,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    stay = body["stay"]
    assert stay["status"] == "pre_checked_in"
    assert stay["pre_checked_in"] is True
    assert stay["registration_signed"] is True

    # Persisted in DB
    erp = connect_erp()
    row = erp.execute(
        "SELECT status, registration_signed_at, pre_check_in_at FROM hospitalityclaw_reservation WHERE id = ?",
        (setup_guest["reservation_id"],),
    ).fetchone()
    assert row["status"] == "pre_checked_in"
    assert row["registration_signed_at"]
    assert row["pre_check_in_at"]
    docs = erp.execute(
        "SELECT COUNT(*) AS c FROM hospitalityclaw_guest_document WHERE reservation_id = ?",
        (setup_guest["reservation_id"],),
    ).fetchone()["c"]
    assert docs >= 1
    erp.close()


def test_guest_id_upload(client, setup_guest):
    token = setup_guest["token"]
    r = client.post(
        f"/api/v1/guest/{token}/id-document",
        files={"file": ("passport.png", io.BytesIO(TINY_PNG), "image/png")},
        data={"doc_type": "passport"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body.get("document_id") or body.get("document", {}).get("id")

    # Upload alone bumps reserved → pre_checked_in
    erp = connect_erp()
    status = erp.execute(
        "SELECT status FROM hospitalityclaw_reservation WHERE id = ?",
        (setup_guest["reservation_id"],),
    ).fetchone()["status"]
    assert status == "pre_checked_in"
    erp.close()


def test_guest_request_checkout_requires_checked_in(client, setup_guest):
    token = setup_guest["token"]
    r = client.post(f"/api/v1/guest/{token}/request-checkout", json={})
    assert r.status_code == 400

    erp = connect_erp()
    erp.execute(
        "UPDATE hospitalityclaw_reservation SET status = 'checked_in' WHERE id = ?",
        (setup_guest["reservation_id"],),
    )
    erp.execute(
        "UPDATE hospitalityclaw_folio SET balance = '0' WHERE id = ?",
        (setup_guest["folio_id"],),
    )
    erp.commit()
    erp.close()

    r2 = client.post(f"/api/v1/guest/{token}/request-checkout", json={})
    assert r2.status_code == 200, r2.text
    assert r2.json()["checkout_requested"] is True

    erp = connect_erp()
    stamp = erp.execute(
        "SELECT checkout_requested_at FROM hospitalityclaw_reservation WHERE id = ?",
        (setup_guest["reservation_id"],),
    ).fetchone()["checkout_requested_at"]
    assert stamp
    erp.close()


def test_guest_document_download(client, setup_guest):
    token = setup_guest["token"]
    up = client.post(
        f"/api/v1/guest/{token}/id-document",
        files={"file": ("id.png", io.BytesIO(TINY_PNG), "image/png")},
        data={"doc_type": "id_photo"},
    )
    assert up.status_code == 200
    doc_id = up.json().get("document_id") or up.json().get("document", {}).get("id")
    assert doc_id

    dl = client.get(f"/api/v1/guest/{token}/documents/{doc_id}")
    assert dl.status_code == 200
    assert dl.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_booking_token_created_on_reservation(setup_guest, monkeypatch):
    """hospitality add-reservation assigns booking_token when column exists."""
    from hospitalityclaw.scripts import db_query as hq

    erp = connect_erp()
    room_id = str(uuid.uuid4())
    rt = erp.execute(
        "SELECT id FROM hospitalityclaw_room_type WHERE hotel_id = ?",
        (setup_guest["hotel_id"],),
    ).fetchone()["id"]
    erp.execute(
        """INSERT INTO hospitalityclaw_room
           (id, tenant_id, hotel_id, room_type_id, room_number, status)
           VALUES (?, ?, ?, ?, '101', 'vacant')""",
        (room_id, setup_guest["tenant_id"], setup_guest["hotel_id"], rt),
    )
    erp.commit()

    class Args:
        tenant_id = setup_guest["tenant_id"]
        hotel_id = setup_guest["hotel_id"]
        guest_id = setup_guest["guest_id"]
        room_type_id = rt
        room_id = None
        check_in = (date.today() + timedelta(days=10)).isoformat()
        check_out = (date.today() + timedelta(days=12)).isoformat()
        adults = "1"
        children = "0"
        notes = None
        rate = None

    captured = {}

    def fake_ok(payload):
        captured.update(payload)

    monkeypatch.setattr(hq, "ok", fake_ok)
    monkeypatch.setattr(
        hq, "err", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a))
    )

    hq.hospitality_add_reservation(erp, Args())
    assert captured.get("booking_token")
    assert captured.get("guest_portal_path", "").startswith("/guest/")
    erp.close()
