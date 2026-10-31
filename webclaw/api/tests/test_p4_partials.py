"""P4 partials — announcement regions, partner console, analytics cohorts/export."""
from __future__ import annotations

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

import db
from auth.passwords import hash_password
from admin_ops import announcement_matches_tenant
from rbac import seed_rbac


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
    try:
        seed_rbac(conn)
    except Exception:
        pass
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


def _login(client, email="admin@test.com", password="AdminPass1!") -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_announcement_region_match_helper():
    assert announcement_matches_tenant(
        audience="all", regions=[], plan_code="starter", country="AE"
    )
    assert announcement_matches_tenant(
        audience="starter", regions=["AE", "US"], plan_code="starter", country="ae"
    )
    assert not announcement_matches_tenant(
        audience="enterprise", regions=["AE"], plan_code="starter", country="AE"
    )
    assert not announcement_matches_tenant(
        audience="all", regions=["US"], plan_code="starter", country="AE"
    )


def test_announcement_regions_crud(client, setup_dbs):
    _seed_admin()
    h = _login(client)
    r = client.post(
        "/api/v1/admin/announcements",
        headers=h,
        json={
            "title": "GCC maintenance",
            "body": "Scheduled window",
            "channel": "banner",
            "audience": "all",
            "status": "published",
            "regions": ["AE", "SA"],
        },
    )
    assert r.status_code == 200, r.text
    ann = r.json()["announcement"]
    assert set(ann["regions"]) == {"AE", "SA"}

    r = client.get("/api/v1/admin/announcements", headers=h)
    assert r.status_code == 200
    found = next(a for a in r.json()["announcements"] if a["id"] == ann["id"])
    assert "AE" in found["regions"]


def test_partner_console_scoped(client, setup_dbs):
    _seed_admin()
    h = _login(client)
    tid = str(uuid.uuid4())

    r = client.post(
        "/api/v1/admin/partners",
        headers=h,
        json={
            "name": "ResellCo",
            "contact_email": "partner@resell.test",
            "commission_percent": 12,
        },
    )
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    r = client.post(f"/api/v1/admin/partners/{pid}/tenants/{tid}", headers=h)
    assert r.status_code == 200, r.text

    r = client.post(f"/api/v1/admin/partners/{pid}/provision-user", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "partner@resell.test"
    user_id = r.json()["user_id"]

    # Set known password for partner user
    conn = db.get_connection()
    conn.execute(
        "UPDATE webclaw_user SET password_hash = ? WHERE id = ?",
        (hash_password("PartnerPass1!"), user_id),
    )
    conn.commit()

    ph = _login(client, email="partner@resell.test", password="PartnerPass1!")
    r = client.get("/api/v1/partner/me", headers=ph)
    assert r.status_code == 200, r.text
    assert r.json()["partner"]["id"] == pid
    assert r.json()["stats"]["tenant_count"] >= 1

    r = client.get("/api/v1/partner/tenants", headers=ph)
    assert r.status_code == 200, r.text
    # Without ERP hotel row, still returns placeholder or empty filtered list
    assert "tenants" in r.json()

    r = client.get("/api/v1/partner/analytics", headers=ph)
    assert r.status_code == 200, r.text
    assert "by_plan" in r.json()["analytics"]


def test_analytics_cohorts_and_export(client, setup_dbs):
    _seed_admin()
    h = _login(client)

    r = client.get("/api/v1/admin/analytics/cohorts?dimension=plan", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["dimension"] == "plan"
    assert "cohorts" in body

    r = client.get("/api/v1/admin/analytics/export.csv?dimension=country", headers=h)
    assert r.status_code == 200, r.text
    assert "text/csv" in r.headers.get("content-type", "")
    text = r.text
    assert "key" in text.splitlines()[0]
