"""Onboarding pipeline — checklist, sync, go-live gates."""
from __future__ import annotations

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

import db
from auth.passwords import hash_password
from admin_onboarding import (
    DEFAULT_STEPS,
    count_onboarding_in_progress,
    go_live,
    start_onboarding,
    sync_onboarding_progress,
)


@pytest.fixture
def client(setup_dbs):
    from main import app

    return TestClient(app)


def _role_id(conn, name: str, *, is_system: int = 0) -> str:
    row = conn.execute("SELECT id FROM webclaw_role WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0] if isinstance(row, tuple) else row["id"]
    role_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO webclaw_role (id, name, description, is_system) VALUES (?, ?, '', ?)",
        (role_id, name, is_system),
    )
    return role_id


def _seed_admin(_web=None) -> dict:
    conn = db.get_connection()
    user_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO webclaw_user (id, username, email, full_name, password_hash, status) "
        "VALUES (?, ?, ?, ?, ?, 'active')",
        (user_id, "admin", "admin@test.com", "Admin", hash_password("AdminPass1!")),
    )
    role_id = _role_id(conn, "System Manager", is_system=1)
    conn.execute(
        "INSERT INTO webclaw_user_role (id, user_id, role_id) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), user_id, role_id),
    )
    conn.commit()
    return {"user_id": user_id, "email": "admin@test.com", "password": "AdminPass1!"}


def _seed_hotel(*, name="Harbor Inn", with_rooms=True) -> tuple[str, str]:
    """Returns (tenant_id, admin_email)."""
    tid = str(uuid.uuid4())
    conn = db.get_skill_db("erpclaw")
    abbr = tid.replace("-", "")[:8]
    admin_email = f"owner@{abbr}.test"
    conn.execute(
        "INSERT INTO company (id, name, abbr) VALUES (?, ?, ?)",
        (tid, f"{name}-{abbr}", abbr),
    )
    conn.execute(
        """INSERT INTO pms_tenant
           (id, company_id, hotel_name, admin_email, timezone, country, currency,
            subscription_tier, status)
           VALUES (?, ?, ?, ?, 'UTC', 'United States', 'USD', 'starter', 'active')""",
        (tid, tid, name, admin_email),
    )
    hotel_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_hotel
           (id, tenant_id, name, currency, timezone)
           VALUES (?, ?, ?, 'USD', 'UTC')""",
        (hotel_id, tid, name),
    )
    if with_rooms:
        room_type_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO hospitalityclaw_room_type
               (id, tenant_id, hotel_id, name, code, base_rate)
               VALUES (?, ?, ?, 'Standard', 'STD', '100.00')""",
            (room_type_id, tid, hotel_id),
        )
        conn.execute(
            """INSERT INTO hospitalityclaw_room
               (id, tenant_id, hotel_id, room_type_id, room_number, status)
               VALUES (?, ?, ?, ?, '101', 'vacant')""",
            (str(uuid.uuid4()), tid, hotel_id, room_type_id),
        )
    conn.commit()
    return tid, admin_email

def _login(client, email, password) -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_start_onboarding_seeds_default_steps(setup_dbs):
    conn = db.get_connection()
    tid = str(uuid.uuid4())
    payload = start_onboarding(
        conn,
        tenant_id=tid,
        hotel_name="Test Inn",
        admin_email="owner@test.com",
        source="admin",
        owner_already_logged_in=False,
    )
    assert payload["status"] in ("in_progress", "ready")
    assert payload["source"] == "admin"
    assert payload["owner_invite_status"] == "pending"
    keys = {s["key"] for s in payload["steps"]}
    assert keys == {s["key"] for s in DEFAULT_STEPS}
    account = next(s for s in payload["steps"] if s["key"] == "account_created")
    assert account["status"] == "done"
    owner = next(s for s in payload["steps"] if s["key"] == "owner_login")
    assert owner["status"] == "pending"


def test_self_serve_marks_owner_login(setup_dbs):
    conn = db.get_connection()
    tid = str(uuid.uuid4())
    payload = start_onboarding(
        conn,
        tenant_id=tid,
        hotel_name="Self Serve Hotel",
        admin_email="me@hotel.com",
        source="self_serve",
        owner_already_logged_in=True,
    )
    assert payload["owner_invite_status"] == "accepted"
    owner = next(s for s in payload["steps"] if s["key"] == "owner_login")
    assert owner["status"] == "done"


def test_sync_autocompletes_rooms_and_go_live(setup_dbs):
    tid, admin_email = _seed_hotel(with_rooms=True)
    conn = db.get_connection()
    uid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO webclaw_user
           (id, username, email, full_name, password_hash, status, tenant_id, last_login)
           VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
        (
            uid,
            "owner",
            admin_email,
            "Owner",
            hash_password("OwnerPass1!"),
            tid,
            "2026-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()

    start_onboarding(
        conn,
        tenant_id=tid,
        hotel_name="Harbor Inn",
        admin_email=admin_email,
        source="admin",
    )
    payload = sync_onboarding_progress(conn, tid)
    by_key = {s["key"]: s["status"] for s in payload["steps"]}
    assert by_key["account_created"] == "done"
    assert by_key["property_profile"] == "done"
    assert by_key["room_types"] == "done"
    assert by_key["rooms"] == "done"
    assert by_key["owner_login"] == "done"
    assert by_key["go_live_confirm"] == "pending"
    assert payload["can_go_live"] is True

    result = go_live(conn, tid, actor_email="admin@test.com")
    assert result["ok"] is True
    assert result["onboarding"]["status"] == "live"
    assert result["onboarding"]["can_go_live"] is False
    assert count_onboarding_in_progress(conn) == 0


def test_go_live_blocked_without_rooms(setup_dbs):
    tid, admin_email = _seed_hotel(with_rooms=False)
    conn = db.get_connection()
    start_onboarding(
        conn,
        tenant_id=tid,
        hotel_name="Empty Inn",
        admin_email=admin_email,
        source="admin",
        owner_already_logged_in=True,
    )
    conn.execute(
        """UPDATE webclaw_onboarding_step SET status='done', completed_at=?, completed_by='t'
           WHERE tenant_id=? AND step_key IN ('owner_login','property_profile','account_created')""",
        ("2026-01-01T00:00:00Z", tid),
    )
    conn.commit()
    payload = sync_onboarding_progress(conn, tid)
    assert payload["can_go_live"] is False
    result = go_live(conn, tid, actor_email="admin@test.com")
    assert result["ok"] is False
    pending = result.get("pending") or []
    assert "rooms" in pending or "room_types" in pending


def test_admin_onboarding_api(client, setup_dbs):
    _seed_admin()
    tid, _email = _seed_hotel(with_rooms=True)
    token = _login(client, "admin@test.com", "AdminPass1!")
    h = _headers(token)

    r = client.get(f"/api/v1/admin/tenants/{tid}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("onboarding")
    assert body["onboarding"]["status"] in ("in_progress", "ready")
    assert len(body["onboarding"]["steps"]) == len(DEFAULT_STEPS)

    r = client.patch(
        f"/api/v1/admin/tenants/{tid}/onboarding/steps/owner_login",
        headers=h,
        json={"status": "done"},
    )
    assert r.status_code == 200, r.text

    r = client.get("/api/v1/admin/onboarding", headers=h)
    assert r.status_code == 200
    assert r.json()["in_progress_count"] >= 1

    onboarding = client.get(f"/api/v1/admin/tenants/{tid}/onboarding", headers=h).json()[
        "onboarding"
    ]
    for step in onboarding["steps"]:
        if step["required"] and step["key"] != "go_live_confirm" and step["status"] != "done":
            client.patch(
                f"/api/v1/admin/tenants/{tid}/onboarding/steps/{step['key']}",
                headers=h,
                json={"status": "done"},
            )

    r = client.post(f"/api/v1/admin/tenants/{tid}/onboarding/go-live", headers=h, json={})
    assert r.status_code == 200, r.text
    assert r.json()["onboarding"]["status"] == "live"

    r = client.get("/api/v1/admin/analytics", headers=h)
    assert r.status_code == 200
    assert r.json().get("onboarding_hotels", 0) == 0
