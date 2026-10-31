"""P0 remaining partials — dashboard depth, soft-cancel, plan limits, billing stubs."""
from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

import db
from auth.passwords import hash_password
from admin_growth import assert_within_plan_limits
from admin_platform import (
    soft_cancel_tenant,
    purge_tenant_hard,
    resolve_lifecycle_phase,
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


def _seed_admin():
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


def _seed_hotel(*, name="P0 Inn", rooms=1) -> str:
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
           VALUES (?, ?, ?, ?, 'UTC', 'United States', 'USD', 'starter', 'active')""",
        (tid, tid, name, f"owner@{abbr}.test"),
    )
    hotel_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_hotel
           (id, tenant_id, name, currency, timezone)
           VALUES (?, ?, ?, 'USD', 'UTC')""",
        (hotel_id, tid, name),
    )
    rt = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_room_type
           (id, tenant_id, hotel_id, name, code, base_rate)
           VALUES (?, ?, ?, 'Standard', 'STD', '100.00')""",
        (rt, tid, hotel_id),
    )
    for i in range(rooms):
        conn.execute(
            """INSERT INTO hospitalityclaw_room
               (id, tenant_id, hotel_id, room_type_id, room_number, status)
               VALUES (?, ?, ?, ?, ?, 'vacant')""",
            (str(uuid.uuid4()), tid, hotel_id, rt, str(100 + i)),
        )
    conn.commit()
    return tid


def _login(client) -> dict:
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "AdminPass1!"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_analytics_includes_p0_dashboard_fields(client, setup_dbs):
    _seed_admin()
    _seed_hotel()
    h = _login(client)
    r = client.get("/api/v1/admin/analytics", headers=h)
    assert r.status_code == 200, r.text
    a = r.json()["analytics"]
    assert "subscription_arr" in a
    assert "new_signups_30d" in a
    assert "churn_30d" in a
    assert "failed_payments_open" in a
    assert "trials_ending_7d" in a
    assert "past_due_hotels" in a
    assert a["subscription_arr"] == round(float(a["subscription_mrr"]) * 12, 2)
    assert "stripe" in a["health"]


def test_soft_cancel_retains_row_and_purge_removes(setup_dbs):
    tid = _seed_hotel(name="Cancel Me")
    result = soft_cancel_tenant(tid, actor_email="admin@test.com")
    assert result["ok"] is True
    assert result["tenant_status"] == "cancelled"
    assert result.get("purge_after")

    erp = db.get_skill_db("erpclaw")
    row = erp.execute("SELECT status FROM pms_tenant WHERE id = ?", (tid,)).fetchone()
    erp.close()
    assert row is not None
    status = row[0] if not hasattr(row, "keys") else row["status"]
    assert status == "cancelled"

    # Hard purge
    purged = purge_tenant_hard(tid, actor_email="admin@test.com")
    assert purged["ok"] is True
    erp = db.get_skill_db("erpclaw")
    gone = erp.execute("SELECT 1 FROM pms_tenant WHERE id = ?", (tid,)).fetchone()
    erp.close()
    assert gone is None


def test_delete_api_soft_cancels(client, setup_dbs):
    _seed_admin()
    tid = _seed_hotel(name="API Cancel")
    h = _login(client)
    r = client.delete(f"/api/v1/admin/tenants/{tid}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["tenant_status"] == "cancelled"
    assert "purge_after" in r.json()
    erp = db.get_skill_db("erpclaw")
    assert erp.execute("SELECT 1 FROM pms_tenant WHERE id = ?", (tid,)).fetchone()
    erp.close()


def test_resolve_lifecycle_phases():
    assert resolve_lifecycle_phase(erp_status="active") == "active"
    assert resolve_lifecycle_phase(erp_status="suspended") == "suspended"
    assert resolve_lifecycle_phase(erp_status="active", stripe_status="past_due") == "past_due"
    assert (
        resolve_lifecycle_phase(erp_status="active", onboarding_status="in_progress")
        == "onboarding"
    )
    assert (
        resolve_lifecycle_phase(
            erp_status="active",
            billing={"trial_ends_at": "2099-01-01T00:00:00+00:00"},
        )
        == "trial"
    )


def test_assert_within_plan_limits_blocks_overage(setup_dbs):
    # starter plan typically max_rooms=10 — seed more rooms than cap after forcing plan
    tid = _seed_hotel(rooms=2)
    conn = db.get_connection()
    # Force tiny room cap on starter
    from admin_platform import ensure_subscription_plans

    ensure_subscription_plans(conn)
    conn.execute(
        "UPDATE webclaw_subscription_plan SET max_rooms = 1, max_staff = 1 WHERE code = 'starter'"
    )
    conn.commit()

    err = assert_within_plan_limits(tid, "rooms", delta=1)
    assert err is not None
    assert err["code"] == "PLAN_LIMIT_EXCEEDED"
    assert err["metric"] == "rooms"

    # Under cap is fine when fewer rooms
    tid2 = _seed_hotel(name="Under Cap", rooms=0)
    ok = assert_within_plan_limits(tid2, "rooms", delta=1)
    assert ok is None


def test_billing_invoices_requires_stripe_or_returns_empty(client, setup_dbs):
    _seed_admin()
    tid = _seed_hotel()
    h = _login(client)
    with patch("stripe_billing.platform_stripe_configured", return_value=False):
        r = client.get(
            f"/api/v1/admin/billing/invoices?tenant_id={tid}",
            headers=h,
        )
    assert r.status_code == 503

    with patch("stripe_billing.platform_stripe_configured", return_value=True), patch(
        "stripe_billing.list_invoices_for_tenant", return_value=[]
    ):
        r = client.get(
            f"/api/v1/admin/billing/invoices?tenant_id={tid}",
            headers=h,
        )
    assert r.status_code == 200
    assert r.json()["invoices"] == []


def test_billing_coupon_create_stub(client, setup_dbs):
    _seed_admin()
    h = _login(client)
    fake = {"coupon_id": "c_1", "promotion_code_id": "p_1", "code": "SAVE10", "percent_off": 10}
    with patch("stripe_billing.platform_stripe_configured", return_value=True), patch(
        "stripe_billing.create_coupon", return_value=fake
    ):
        r = client.post(
            "/api/v1/admin/billing/coupons",
            headers=h,
            json={"code": "SAVE10", "percent_off": 10},
        )
    assert r.status_code == 200, r.text
    assert r.json()["coupon"]["code"] == "SAVE10"
