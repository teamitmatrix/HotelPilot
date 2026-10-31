"""Hotel staff user management API — tenant-scoped CRUD."""
from __future__ import annotations

import os
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pgutil import connect_web

from auth.passwords import hash_password
from auth.jwt_utils import create_access_token, get_signing_secret
from rbac import FRONT_DESK, GENERAL_MANAGER, HOTEL_MANAGER, HOTEL_OWNER, seed_rbac
from tenant import upsert_membership


@pytest.fixture()
def hotel_env(setup_test_db, monkeypatch):
    monkeypatch.setenv("WEBCLAW_ENV", "development")

    conn = connect_web()
    seed_rbac(conn)

    tenant_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    desk_id = str(uuid.uuid4())
    now = "2026-09-28T00:00:00+00:00"
    secret = get_signing_secret(conn)

    owner_role = conn.execute(
        "SELECT id FROM webclaw_role WHERE code = ?", (HOTEL_OWNER,)
    ).fetchone()[0]
    desk_role = conn.execute(
        "SELECT id FROM webclaw_role WHERE code = ?", (FRONT_DESK,)
    ).fetchone()[0]

    conn.execute(
        """INSERT INTO webclaw_user
           (id, username, email, full_name, password_hash, status, created_at, tenant_id)
           VALUES (?, 'owner', 'owner@hotel.test', 'Owner', ?, 'active', ?, ?)""",
        (owner_id, hash_password("OwnerPass1"), now, tenant_id),
    )
    conn.execute(
        "INSERT INTO webclaw_user_role (id, user_id, role_id, tenant_id) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), owner_id, owner_role, tenant_id),
    )
    upsert_membership(
        conn, owner_id, tenant_id,
        role_code=HOTEL_OWNER, role_name="Hotel Owner", hotel_name="Test Inn",
    )

    conn.execute(
        """INSERT INTO webclaw_user
           (id, username, email, full_name, password_hash, status, created_at, tenant_id)
           VALUES (?, 'desk', 'desk@hotel.test', 'Desk', ?, 'active', ?, ?)""",
        (desk_id, hash_password("DeskPass1"), now, tenant_id),
    )
    conn.execute(
        "INSERT INTO webclaw_user_role (id, user_id, role_id, tenant_id) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), desk_id, desk_role, tenant_id),
    )
    upsert_membership(
        conn, desk_id, tenant_id,
        role_code=FRONT_DESK, role_name="Front Desk", hotel_name="Test Inn",
    )
    conn.commit()

    from main import app
    client = TestClient(app)

    owner_token = create_access_token(
        owner_id, "owner@hotel.test", secret, tenant_id=tenant_id,
    )
    desk_token = create_access_token(
        desk_id, "desk@hotel.test", secret, tenant_id=tenant_id,
    )

    return {
        "client": client,
        "tenant_id": tenant_id,
        "owner_id": owner_id,
        "desk_id": desk_id,
        "owner_headers": {
            "Authorization": f"Bearer {owner_token}",
            "X-Tenant-Id": tenant_id,
        },
        "desk_headers": {
            "Authorization": f"Bearer {desk_token}",
            "X-Tenant-Id": tenant_id,
        },
    }


def test_owner_lists_tenant_staff(hotel_env):
    client = hotel_env["client"]
    resp = client.get("/api/v1/hotel/users", headers=hotel_env["owner_headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    emails = {u["email"] for u in body["users"]}
    assert "owner@hotel.test" in emails
    assert "desk@hotel.test" in emails
    codes = {r["code"] for r in body["assignable_roles"]}
    assert codes == {
        "HOTEL_ADMIN",
        "GENERAL_MANAGER",
        "HOTEL_MANAGER",
        "FRONT_DESK",
        "HOUSEKEEPING",
        "HOTEL_ACCOUNTANT",
        "ACCOUNTANT",
    }


def test_front_desk_cannot_manage_staff(hotel_env):
    client = hotel_env["client"]
    resp = client.get("/api/v1/hotel/users", headers=hotel_env["desk_headers"])
    assert resp.status_code == 403


def test_owner_creates_and_revokes_staff(hotel_env):
    client = hotel_env["client"]
    headers = hotel_env["owner_headers"]
    create = client.post(
        "/api/v1/hotel/users",
        headers=headers,
        json={
            "full_name": "House Keeper",
            "email": "hk@hotel.test",
            "password": "TempPass1",
            "role": "HOUSEKEEPING",
        },
    )
    assert create.status_code == 200, create.text
    user = create.json()["user"]
    assert user["role_code"] == "HOUSEKEEPING"
    assert user["status"] == "active"

    patch = client.patch(
        f"/api/v1/hotel/users/{user['id']}",
        headers=headers,
        json={"role": "ACCOUNTANT", "status": "active"},
    )
    assert patch.status_code == 200
    assert patch.json()["user"]["role_code"] == "ACCOUNTANT"

    revoke = client.delete(f"/api/v1/hotel/users/{user['id']}", headers=headers)
    assert revoke.status_code == 200
    assert revoke.json()["user"]["status"] == "disabled"


def _staff_headers(conn, tenant_id, email, role_code, password="StaffPass1"):
    from auth.jwt_utils import create_access_token, get_signing_secret

    user_id = str(uuid.uuid4())
    role_id = conn.execute(
        "SELECT id FROM webclaw_role WHERE code = ?", (role_code,)
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO webclaw_user
           (id, username, email, full_name, password_hash, status, created_at, tenant_id)
           VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
        (
            user_id,
            email.split("@")[0],
            email,
            email,
            hash_password(password),
            "2026-09-28T00:00:00+00:00",
            tenant_id,
        ),
    )
    conn.execute(
        "INSERT INTO webclaw_user_role (id, user_id, role_id, tenant_id) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, role_id, tenant_id),
    )
    upsert_membership(
        conn,
        user_id,
        tenant_id,
        role_code=role_code,
        role_name=role_code,
        hotel_name="Test Inn",
    )
    conn.commit()
    secret = get_signing_secret(conn)
    token = create_access_token(user_id, email, secret, tenant_id=tenant_id)
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
    }, user_id


def test_manager_assigns_branch_staff_only(hotel_env, setup_test_db):
    from pgutil import connect_web

    conn = connect_web()
    seed_rbac(conn)
    tenant_id = hotel_env["tenant_id"]
    headers, _ = _staff_headers(conn, tenant_id, "mgr@hotel.test", HOTEL_MANAGER)
    client = hotel_env["client"]

    listed = client.get("/api/v1/hotel/users", headers=headers)
    assert listed.status_code == 200
    codes = {r["code"] for r in listed.json()["assignable_roles"]}
    assert codes == {"FRONT_DESK", "HOUSEKEEPING", "ACCOUNTANT"}

    ok = client.post(
        "/api/v1/hotel/users",
        headers=headers,
        json={
            "full_name": "Branch Acc",
            "email": "branch-acc@hotel.test",
            "password": "TempPass1",
            "role": "ACCOUNTANT",
        },
    )
    assert ok.status_code == 200, ok.text

    denied = client.post(
        "/api/v1/hotel/users",
        headers=headers,
        json={
            "full_name": "Bad Admin",
            "email": "bad-admin@hotel.test",
            "password": "TempPass1",
            "role": "HOTEL_ADMIN",
        },
    )
    assert denied.status_code == 403


def test_gm_cannot_create_admin_or_hotel_accountant(hotel_env):
    from pgutil import connect_web

    conn = connect_web()
    seed_rbac(conn)
    tenant_id = hotel_env["tenant_id"]
    headers, _ = _staff_headers(conn, tenant_id, "gm@hotel.test", GENERAL_MANAGER)
    client = hotel_env["client"]

    listed = client.get("/api/v1/hotel/users", headers=headers)
    assert listed.status_code == 200
    codes = {r["code"] for r in listed.json()["assignable_roles"]}
    assert codes == {"HOTEL_MANAGER", "FRONT_DESK", "HOUSEKEEPING", "ACCOUNTANT"}

    denied_admin = client.post(
        "/api/v1/hotel/users",
        headers=headers,
        json={
            "full_name": "No Admin",
            "email": "no-admin@hotel.test",
            "password": "TempPass1",
            "role": "HOTEL_ADMIN",
        },
    )
    assert denied_admin.status_code == 403

    denied_ha = client.post(
        "/api/v1/hotel/users",
        headers=headers,
        json={
            "full_name": "No HA",
            "email": "no-ha@hotel.test",
            "password": "TempPass1",
            "role": "HOTEL_ACCOUNTANT",
        },
    )
    assert denied_ha.status_code == 403

    ok = client.post(
        "/api/v1/hotel/users",
        headers=headers,
        json={
            "full_name": "Branch Mgr",
            "email": "branch-mgr@hotel.test",
            "password": "TempPass1",
            "role": "HOTEL_MANAGER",
        },
    )
    assert ok.status_code == 200, ok.text
