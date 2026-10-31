"""P2 partials — user directory, audit diffs, ticket SLA/macros, IP allowlist."""
from __future__ import annotations

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

import db
from auth.passwords import hash_password
from admin_ops import (
    check_sla_breaches,
    ensure_ops_tables,
    ip_in_allowlist,
    write_platform_audit,
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
    return user_id


def _seed_staff(*, email="staff@test.com", password="StaffPass1!") -> str:
    conn = db.get_connection()
    user_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO webclaw_user (id, username, email, full_name, password_hash, status) "
        "VALUES (?, ?, ?, ?, ?, 'active')",
        (user_id, email.split("@")[0], email, "Staff", hash_password(password)),
    )
    conn.commit()
    return user_id


def _login(client, email="admin@test.com", password="AdminPass1!") -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_user_directory_search_force_logout_reset(client, setup_dbs):
    _seed_admin()
    staff_id = _seed_staff(email="desk@hotel.test")
    # Create a session for staff
    conn = db.get_connection()
    conn.execute(
        """INSERT INTO webclaw_session (id, user_id, refresh_token_hash, expires_at, ip_address)
           VALUES (?, ?, 'hash', '2099-01-01T00:00:00+00:00', '1.2.3.4')""",
        (str(uuid.uuid4()), staff_id),
    )
    conn.commit()

    h = _login(client)
    r = client.get("/api/v1/admin/users?q=desk@hotel", headers=h)
    assert r.status_code == 200, r.text
    users = r.json()["users"]
    assert any(u["id"] == staff_id for u in users)
    match = next(u for u in users if u["id"] == staff_id)
    assert "tenants" in match
    assert match["active_sessions"] >= 1

    r = client.post(f"/api/v1/admin/users/{staff_id}/force-logout", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["sessions_revoked"] >= 1
    left = conn.execute(
        "SELECT COUNT(*) FROM webclaw_session WHERE user_id = ?", (staff_id,)
    ).fetchone()[0]
    assert int(left or 0) == 0

    r = client.post(f"/api/v1/admin/users/{staff_id}/reset-password", headers=h, json={})
    assert r.status_code == 200, r.text
    assert r.json().get("generated") is True
    assert r.json().get("temporary_password")


def test_audit_log_before_after_diff(client, setup_dbs):
    _seed_admin()
    conn = db.get_connection()
    ensure_ops_tables(conn)
    write_platform_audit(
        conn,
        user_id="u1",
        user_email="admin@test.com",
        tenant_id=None,
        action_category="users",
        action="update_user",
        ip_address="127.0.0.1",
        detail="test",
        before={"status": "active"},
        after={"status": "disabled"},
    )
    h = _login(client)
    r = client.get("/api/v1/admin/audit-logs?category=users", headers=h)
    assert r.status_code == 200, r.text
    logs = r.json()["logs"]
    assert logs
    hit = next((x for x in logs if x.get("has_diff")), None)
    assert hit is not None
    assert hit["before"]["status"] == "active"
    assert hit["after"]["status"] == "disabled"


def test_support_sla_and_macros(client, setup_dbs):
    _seed_admin()
    tid = str(uuid.uuid4())
    erp = db.get_skill_db("erpclaw")
    abbr = tid.replace("-", "")[:8]
    erp.execute(
        "INSERT INTO company (id, name, abbr) VALUES (?, ?, ?)",
        (tid, f"SLA-{abbr}", abbr),
    )
    erp.execute(
        """INSERT INTO pms_tenant
           (id, company_id, hotel_name, admin_email, timezone, country, currency,
            subscription_tier, status)
           VALUES (?, ?, ?, ?, 'UTC', 'United States', 'USD', 'starter', 'active')""",
        (tid, tid, "SLA Hotel", f"o@{abbr}.test"),
    )
    erp.commit()
    erp.close()

    h = _login(client)
    r = client.post(
        "/api/v1/admin/support-tickets",
        headers=h,
        json={"tenant_id": tid, "subject": "WiFi down", "priority": "urgent"},
    )
    assert r.status_code == 200, r.text
    ticket = r.json()["ticket"]
    assert ticket.get("sla_due_at")
    assert ticket.get("sla_breached") is False
    ticket_id = ticket["id"]

    r = client.get("/api/v1/admin/support-macros", headers=h)
    assert r.status_code == 200
    macros = r.json()["macros"]
    assert len(macros) >= 1
    macro_id = macros[0]["id"]

    r = client.post(
        f"/api/v1/admin/support-tickets/{ticket_id}/apply-macro",
        headers=h,
        json={"macro_id": macro_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ticket"].get("first_response_at")

    # Force SLA breach
    conn = db.get_connection()
    conn.execute(
        "UPDATE webclaw_support_ticket SET sla_due_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (ticket_id,),
    )
    conn.commit()
    n = check_sla_breaches(conn)
    assert n >= 1
    row = conn.execute(
        "SELECT sla_breached FROM webclaw_support_ticket WHERE id = ?", (ticket_id,)
    ).fetchone()
    assert bool(row[0] if isinstance(row, tuple) else row["sla_breached"])


def test_ip_allowlist_blocks_login(client, setup_dbs):
    _seed_admin()
    assert ip_in_allowlist("10.0.0.5", ["10.0.0.0/8"]) is True
    assert ip_in_allowlist("11.0.0.5", ["10.0.0.0/8"]) is False

    h = _login(client)
    r = client.put(
        "/api/v1/admin/security-policy",
        headers=h,
        json={
            "ip_allowlist_enabled": True,
            "ip_allowlist": ["203.0.113.0/24"],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["policy"]["ip_allowlist_enabled"] is True

    # TestClient typically uses 127.0.0.1 — not in allowlist
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "AdminPass1!"},
    )
    assert r.status_code == 403
    assert r.json().get("code") == "IP_NOT_ALLOWED"

    # Disable allowlist so other tests / cleanup aren't blocked
    r = client.put(
        "/api/v1/admin/security-policy",
        headers={"X-Forwarded-For": "203.0.113.10", **h},
        json={"ip_allowlist_enabled": False},
    )
    # May fail auth if token still works — token should still work; IP check is login-only
    # Re-enable path: use DB directly
    conn = db.get_connection()
    ensure_ops_tables(conn)
    conn.execute(
        "UPDATE webclaw_security_policy SET ip_allowlist_enabled = 0 WHERE id = 'default'"
    )
    conn.commit()
