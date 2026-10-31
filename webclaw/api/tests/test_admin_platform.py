"""Tests for SuperAdmin platform API — tenants, plans, analytics, impersonation.

Uses Postgres via ``tests/conftest.py`` (``setup_dbs`` truncates webclaw + public).
"""
import os
import sys
import uuid

import jwt
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from auth.passwords import hash_password
from auth.jwt_utils import ALGORITHM
import db


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


def _seed_staff(_web=None) -> dict:
    conn = db.get_connection()
    user_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO webclaw_user (id, username, email, full_name, password_hash, status) "
        "VALUES (?, ?, ?, ?, ?, 'active')",
        (user_id, "staff", "staff@test.com", "Staff", hash_password("StaffPass1!")),
    )
    role_id = _role_id(conn, "Hotel Owner", is_system=0)
    conn.execute(
        "INSERT INTO webclaw_user_role (id, user_id, role_id) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), user_id, role_id),
    )
    conn.commit()
    return {"email": "staff@test.com", "password": "StaffPass1!"}


def _seed_hotel(_erp=None, *, name="Harbor Inn", tier="starter", status="active") -> str:
    tid = str(uuid.uuid4())
    conn = db.get_skill_db("erpclaw")
    abbr = tid.replace("-", "")[:8]
    conn.execute(
        "INSERT INTO company (id, name, abbr) VALUES (?, ?, ?)",
        (tid, f"{name}-{abbr}", abbr),
    )
    conn.execute(
        """INSERT INTO pms_tenant
           (id, company_id, hotel_name, admin_email, timezone, country, currency,
            subscription_tier, status)
           VALUES (?, ?, ?, ?, 'UTC', 'United States', 'USD', ?, ?)""",
        (tid, tid, name, f"owner@{name.lower().replace(' ', '')}.test", tier, status),
    )
    hotel_id = str(uuid.uuid4())
    room_type_id = str(uuid.uuid4())
    room_id = str(uuid.uuid4())
    guest_id = str(uuid.uuid4())
    reservation_id = str(uuid.uuid4())
    folio_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_hotel
           (id, tenant_id, name, currency, timezone)
           VALUES (?, ?, ?, 'USD', 'UTC')""",
        (hotel_id, tid, name),
    )
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
        (room_id, tid, hotel_id, room_type_id),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_guest
           (id, tenant_id, first_name, last_name)
           VALUES (?, ?, 'Ada', 'Guest')""",
        (guest_id, tid),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_reservation
           (id, tenant_id, hotel_id, guest_id, room_id, room_type_id,
            check_in, check_out, adults, status, rate)
           VALUES (?, ?, ?, ?, ?, ?, '2026-10-01', '2026-10-02', 1, 'reserved', '120.50')""",
        (reservation_id, tid, hotel_id, guest_id, room_id, room_type_id),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_folio
           (id, tenant_id, reservation_id, guest_id, status, balance)
           VALUES (?, ?, ?, ?, 'open', '120.50')""",
        (folio_id, tid, reservation_id, guest_id),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_folio_charge
           (id, tenant_id, folio_id, description, amount, charge_type, posting_date)
           VALUES (?, ?, ?, 'Room tariff', '120.50', 'room', '2026-10-01')""",
        (str(uuid.uuid4()), tid, folio_id),
    )
    conn.commit()
    return tid


def _login(client, email, password) -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_staff_cannot_list_tenants(client, setup_dbs):
    _seed_admin(setup_dbs["web"])
    staff = _seed_staff(setup_dbs["web"])
    token = _login(client, staff["email"], staff["password"])
    r = client.get("/api/v1/admin/tenants", headers=_headers(token))
    assert r.status_code == 403


def test_list_create_pause_resume_delete_tenants(client, setup_dbs, monkeypatch):
    _seed_admin(setup_dbs["web"])
    token = _login(client, "admin@test.com", "AdminPass1!")
    tid = _seed_hotel(setup_dbs["erp"])

    r = client.get("/api/v1/admin/tenants", headers=_headers(token))
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert data["tenants"][0]["id"] == tid
    assert data["tenants"][0]["plan_code"] == "starter"
    assert data["tenants"][0]["room_count"] == 1

    async def fake_register(**kwargs):
        new_id = str(uuid.uuid4())
        erp = db.get_skill_db("erpclaw")
        abbr = new_id.replace("-", "")[:8]
        erp.execute(
            "INSERT INTO company (id, name, abbr) VALUES (?, ?, ?)",
            (new_id, f"{kwargs['hotel_name']}-{abbr}", abbr),
        )
        erp.execute(
            """INSERT INTO pms_tenant
               (id, company_id, hotel_name, admin_email, timezone, country, currency,
                subscription_tier, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
            (
                new_id, new_id, kwargs["hotel_name"], kwargs["admin_email"],
                kwargs["timezone_name"], kwargs["country"], kwargs["currency"],
                kwargs["subscription_tier"],
            ),
        )
        erp.commit()
        return {
            "status": "ok",
            "http_status": 200,
            "tenant": {"tenant_id": new_id, "hotel_name": kwargs["hotel_name"]},
            "access_token": "should-not-leak",
            "refresh_token": "should-not-leak",
        }

    monkeypatch.setattr("auth.onboarding.register_hotel", fake_register)
    r = client.post(
        "/api/v1/admin/tenants",
        headers=_headers(token),
        json={
            "hotel_name": "Pine Lodge",
            "admin_email": "owner@pinelodge.test",
            "password": "LodgePass1!",
            "plan_code": "business",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "access_token" not in body
    assert body["tenant"]["plan_code"] == "business"

    r = client.post(f"/api/v1/admin/tenants/{tid}/pause", headers=_headers(token))
    assert r.status_code == 200
    assert r.json()["tenant_status"] == "suspended"

    r = client.post(f"/api/v1/admin/tenants/{tid}/resume", headers=_headers(token))
    assert r.status_code == 200
    assert r.json()["tenant_status"] == "active"

    r = client.patch(
        f"/api/v1/admin/tenants/{tid}",
        headers=_headers(token),
        json={"plan_code": "enterprise"},
    )
    assert r.status_code == 200
    assert r.json()["tenant"]["plan_code"] == "enterprise"

    r = client.delete(f"/api/v1/admin/tenants/{tid}", headers=_headers(token))
    assert r.status_code == 200
    listed = client.get("/api/v1/admin/tenants", headers=_headers(token)).json()["tenants"]
    assert all(t["id"] != tid for t in listed)


def test_plans_seed_and_feature_toggles(client, setup_dbs):
    _seed_admin(setup_dbs["web"])
    token = _login(client, "admin@test.com", "AdminPass1!")
    r = client.get("/api/v1/admin/plans", headers=_headers(token))
    assert r.status_code == 200
    data = r.json()
    codes = {p["code"] for p in data["plans"]}
    assert codes == {"starter", "business", "enterprise"}
    assert len(data["feature_catalog"]) >= 5
    business = next(p for p in data["plans"] if p["code"] == "business")
    assert business["billing_cycle"] in ("monthly", "annual")
    assert business["max_rooms"] > 0
    assert business["features"]["reports"] is True

    r = client.put(
        f"/api/v1/admin/plans/{business['id']}",
        headers=_headers(token),
        json={"features": {**business["features"], "api_access": True}, "max_rooms": 80},
    )
    assert r.status_code == 200
    assert r.json()["plan"]["features"]["api_access"] is True
    assert r.json()["plan"]["max_rooms"] == 80

    r = client.post(
        "/api/v1/admin/plans",
        headers=_headers(token),
        json={
            "code": "partner",
            "name": "Partner",
            "billing_cycle": "annual",
            "monthly_price": "99",
            "annual_price": "990",
            "max_rooms": 40,
            "features": {"front_desk": True},
        },
    )
    assert r.status_code == 200
    partner_id = r.json()["plan"]["id"]
    r = client.delete(f"/api/v1/admin/plans/{partner_id}", headers=_headers(token))
    assert r.status_code == 200

    r = client.delete(f"/api/v1/admin/plans/{business['id']}", headers=_headers(token))
    assert r.status_code == 403


def test_analytics_counts(client, setup_dbs):
    _seed_admin(setup_dbs["web"])
    token = _login(client, "admin@test.com", "AdminPass1!")
    _seed_hotel(setup_dbs["erp"], name="One")
    _seed_hotel(setup_dbs["erp"], name="Two", status="suspended")
    r = client.get("/api/v1/admin/analytics", headers=_headers(token))
    assert r.status_code == 200
    a = r.json()["analytics"]
    assert a["total_hotels"] == 2
    assert a["active_hotels"] == 1
    assert a["suspended_hotels"] == 1
    assert a["booking_count"] == 2
    assert a["active_bookings"] == 2
    assert a["total_rooms"] == 2
    assert a["revenue"] == 241.0
    assert a["subscription_mrr"] > 0
    assert a["active_subscriptions"] == 1
    assert "health" in a
    assert a["health"]["status"] in ("operational", "degraded")
    assert "activity" in a
    assert isinstance(a["activity"], list)


def test_impersonate_hotel(client, setup_dbs):
    admin = _seed_admin(setup_dbs["web"])
    token = _login(client, "admin@test.com", "AdminPass1!")
    tid = _seed_hotel(setup_dbs["erp"], name="View Hotel")

    missing = client.post(
        f"/api/v1/admin/tenants/{tid}/impersonate",
        headers=_headers(token),
        json={},
    )
    assert missing.status_code == 400

    reason = "Investigating front-desk check-in defect"
    r = client.post(
        f"/api/v1/admin/tenants/{tid}/impersonate",
        headers=_headers(token),
        json={"reason": reason},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["user"]["is_impersonating"] is True
    assert data["user"]["is_superadmin"] is False
    assert data["user"]["tenant_id"] == tid
    assert data["user"]["impersonation_reason"] == reason
    payload = jwt.decode(
        data["access_token"],
        options={"verify_signature": False},
        algorithms=["HS256"],
    )
    assert payload["impersonate_tenant_id"] == tid
    assert payload["is_impersonating"] is True
    assert payload["impersonation_reason"] == reason

    me = client.get("/api/v1/auth/me", headers=_headers(data["access_token"]))
    assert me.status_code == 200
    user = me.json()["user"]
    assert user["is_impersonating"] is True
    assert user["tenant_id"] == tid
    assert user["is_superadmin"] is False
    assert user.get("impersonation_reason") == reason

    # Impersonation token cannot hit platform admin routes.
    denied = client.get("/api/v1/admin/tenants", headers=_headers(data["access_token"]))
    assert denied.status_code == 403
    assert admin["user_id"] == payload["sub"]

    # High-risk profile change blocked while impersonating.
    blocked = client.put(
        "/api/v1/auth/profile",
        headers=_headers(data["access_token"]),
        json={"full_name": "Should Fail"},
    )
    assert blocked.status_code == 403

    ended = client.post(
        "/api/v1/auth/impersonate/end",
        headers=_headers(data["access_token"]),
    )
    assert ended.status_code == 200, ended.text


def test_tenant_detail_and_support_tickets(client, setup_dbs):
    _seed_admin(setup_dbs["web"])
    token = _login(client, "admin@test.com", "AdminPass1!")
    tid = _seed_hotel(setup_dbs["erp"], name="Detail Hotel")

    detail = client.get(f"/api/v1/admin/tenants/{tid}", headers=_headers(token))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["tenant"]["hotel_name"] == "Detail Hotel"
    assert "usage" in body
    assert "rooms" in body["usage"]
    assert "staff" in body["usage"]

    created = client.post(
        "/api/v1/admin/support-tickets",
        headers=_headers(token),
        json={
            "tenant_id": tid,
            "subject": "OTA sync stuck",
            "body": "Rates not updating on Booking.com",
            "priority": "high",
        },
    )
    assert created.status_code == 200, created.text
    ticket_id = created.json()["ticket"]["id"]

    listed = client.get(
        "/api/v1/admin/support-tickets?status=open",
        headers=_headers(token),
    )
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1

    patched = client.patch(
        f"/api/v1/admin/support-tickets/{ticket_id}",
        headers=_headers(token),
        json={"status": "in_progress", "note": "Checking channel credentials"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["ticket"]["status"] == "in_progress"
    assert len(patched.json()["ticket"]["notes"]) == 1

    overview = client.get("/api/v1/admin/billing/overview", headers=_headers(token))
    assert overview.status_code == 200, overview.text
    assert "at_risk" in overview.json()
    assert "failed_events" in overview.json()
    assert "trials_ending" in overview.json()
