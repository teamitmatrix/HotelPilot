"""WebClaw tenant isolation: JWT claims, payload mismatch, SuperAdmin bypass.

Uses Postgres via ``tests/conftest.py``.
"""
import os
import sys
import uuid

import jwt
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from auth.passwords import hash_password
from auth.jwt_utils import create_access_token, ALGORITHM
from tenant import TenantContext, TenantIsolationError, enforce_payload_tenant, require_tenant_for_action


@pytest.fixture
def client(setup_test_db):
    from main import app

    return TestClient(app)


def _user(conn, email, password, role_name, tenant_id=None):
    user_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO webclaw_user (id, username, email, full_name, password_hash, status, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?)",
        (user_id, email.split("@")[0], email, email, hash_password(password), tenant_id),
    )
    role = conn.execute("SELECT id FROM webclaw_role WHERE name = ?", (role_name,)).fetchone()
    if not role:
        role_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO webclaw_role (id, name, description, is_system) VALUES (?, ?, '', 1)",
            (role_id, role_name),
        )
    else:
        role_id = role[0]
    conn.execute(
        "INSERT INTO webclaw_user_role (id, user_id, role_id) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), user_id, role_id),
    )
    conn.commit()
    return {"user_id": user_id, "email": email, "password": password, "tenant_id": tenant_id}


def test_jwt_contains_tenant_claims():
    token = create_access_token("u1", "a@b.c", "secret", tenant_id="hotel-1", is_superadmin=False)
    payload = jwt.decode(token, "secret", algorithms=[ALGORITHM])
    assert payload["tenant_id"] == "hotel-1"
    assert payload["is_superadmin"] is False


def test_hotel_payload_mismatch_rejected():
    ctx = TenantContext(
        user_id="u1", email="a@b.c", tenant_id="hotel-a",
        is_superadmin=False, roles=("Hotel Owner",),
    )
    with pytest.raises(TenantIsolationError):
        enforce_payload_tenant(ctx, {"company-id": "hotel-b"})


def test_hotel_payload_bound_to_session_tenant():
    ctx = TenantContext(
        user_id="u1", email="a@b.c", tenant_id="hotel-a",
        is_superadmin=False, roles=("Hotel Owner",),
    )
    bound = enforce_payload_tenant(ctx, {"name": "Ada"})
    assert bound["company-id"] == "hotel-a"
    assert bound["tenant-id"] == "hotel-a"


def test_hospitality_hotel_id_is_not_a_tenant_key():
    """hospitalityclaw_hotel.id differs from ERP company/tenant UUID."""
    ctx = TenantContext(
        user_id="u1", email="a@b.c", tenant_id="tenant-company-uuid",
        is_superadmin=False, roles=("Hotel Owner",),
    )
    hospitality_hotel = "hospitality-hotel-uuid"
    bound = enforce_payload_tenant(
        ctx,
        {
            "hotel_id": hospitality_hotel,
            "room_number": "101",
            "room_type_id": "rt-1",
        },
    )
    assert bound["hotel_id"] == hospitality_hotel
    assert bound["company-id"] == "tenant-company-uuid"
    assert bound["tenant-id"] == "tenant-company-uuid"
    assert bound["room_number"] == "101"


def test_superadmin_may_omit_tenant():
    ctx = TenantContext(
        user_id="u1", email="admin@x", tenant_id=None,
        is_superadmin=True, roles=("System Manager",),
    )
    require_tenant_for_action(ctx, "erpclaw", "list-companies")
    bound = enforce_payload_tenant(ctx, {"limit": "10"})
    assert "company-id" not in bound


def test_unbound_user_blocked_from_hotel_action():
    ctx = TenantContext(
        user_id="u1", email="x@y.z", tenant_id=None,
        is_superadmin=False, roles=("Viewer",),
    )
    with pytest.raises(TenantIsolationError):
        require_tenant_for_action(ctx, "hospitalityclaw", "hospitality-list-rooms")


def test_login_returns_tenant(client):
    from db import get_connection
    conn = get_connection()
    tenant = str(uuid.uuid4())
    _user(conn, "owner@hotel.test", "HotelPass1", "Hotel Owner", tenant_id=tenant)
    resp = client.post("/api/v1/auth/login", json={
        "email": "owner@hotel.test", "password": "HotelPass1",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["tenant_id"] == tenant
    assert data["user"]["is_superadmin"] is False
    from db import get_connection as _gc
    from auth.jwt_utils import get_signing_secret
    secret = get_signing_secret(_gc())
    payload = jwt.decode(data["access_token"], secret, algorithms=[ALGORITHM])
    assert payload["tenant_id"] == tenant


def test_register_hotel_validation(client):
    resp = client.post("/api/v1/auth/register-hotel", json={
        "hotel_name": "X",
        "admin_email": "not-an-email",
        "password": "short",
    })
    assert resp.status_code == 400


def test_auth_deps_require_hotel_tenant():
    from auth.deps import require_hotel_tenant
    from fastapi import HTTPException

    ctx = TenantContext(
        user_id="u1", email="a@b.c", tenant_id="hotel-a",
        is_superadmin=False, roles=("Hotel Owner",),
    )
    assert require_hotel_tenant(ctx) == "hotel-a"

    admin = TenantContext(
        user_id="u2", email="admin@x", tenant_id=None,
        is_superadmin=True, roles=("System Manager",),
    )
    with pytest.raises(HTTPException) as exc:
        require_hotel_tenant(admin)
    assert exc.value.status_code == 400


def test_tenant_query_appends_predicate():
    from db_access import TenantQuery, tenant_where

    assert tenant_where("SELECT * FROM webclaw_user") == (
        "SELECT * FROM webclaw_user WHERE tenant_id = ?"
    )
    assert "AND tenant_id = ?" in tenant_where(
        "SELECT * FROM webclaw_user WHERE status = ?"
    )

    from db import get_connection
    from auth.passwords import hash_password

    conn = get_connection()
    conn.execute(
        """INSERT INTO webclaw_user
           (id, username, email, full_name, password_hash, status, tenant_id)
           VALUES ('1', 'a', 'a@x', 'A', ?, 'active', 'hotel-a')""",
        (hash_password("x"),),
    )
    conn.execute(
        """INSERT INTO webclaw_user
           (id, username, email, full_name, password_hash, status, tenant_id)
           VALUES ('2', 'b', 'b@x', 'B', ?, 'active', 'hotel-b')""",
        (hash_password("x"),),
    )
    conn.commit()
    q = TenantQuery(conn, "hotel-a")
    rows = q.execute("SELECT email FROM webclaw_user").fetchall()
    assert [r[0] for r in rows] == ["a@x"]


def test_login_reports_role_and_rejects_foreign_tenant_header(client):
    from db import get_connection
    conn = get_connection()
    tenant = str(uuid.uuid4())
    _user(conn, "desk@hotel.test", "HotelPass1", "Front Desk", tenant_id=tenant)
    resp = client.post("/api/v1/auth/login", json={
        "email": "desk@hotel.test", "password": "HotelPass1",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["role"] == "FRONT_DESK"
    assert body["user"]["tenant_id"] == tenant
    assert body["user"]["tenants"][0]["tenant_id"] == tenant
    token = body["access_token"]

    ok = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant},
    )
    assert ok.status_code == 200
    assert ok.json()["user"]["tenant_id"] == tenant

    foreign = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": str(uuid.uuid4())},
    )
    assert foreign.status_code == 403


def test_switch_tenant_binds_header_for_member_properties(client):
    from db import get_connection
    from tenant import upsert_membership

    conn = get_connection()
    home = str(uuid.uuid4())
    other = str(uuid.uuid4())
    _user(conn, "region@hotels.test", "HotelPass1", "Hotel Manager", tenant_id=home)
    user = conn.execute(
        "SELECT id FROM webclaw_user WHERE email = ?", ("region@hotels.test",)
    ).fetchone()
    upsert_membership(
        conn, user["id"], other,
        role_code="HOTEL_MANAGER", role_name="Hotel Manager", hotel_name="North Inn",
    )
    upsert_membership(
        conn, user["id"], home,
        role_code="HOTEL_MANAGER", role_name="Hotel Manager", hotel_name="South Inn",
    )
    conn.commit()

    login = client.post("/api/v1/auth/login", json={
        "email": "region@hotels.test", "password": "HotelPass1",
    })
    assert login.status_code == 200
    token = login.json()["access_token"]
    assert {t["tenant_id"] for t in login.json()["user"]["tenants"]} == {home, other}

    denied = client.post(
        "/api/v1/auth/switch-tenant",
        headers={"Authorization": f"Bearer {token}"},
        json={"tenant_id": str(uuid.uuid4())},
    )
    assert denied.status_code == 403

    switched = client.post(
        "/api/v1/auth/switch-tenant",
        headers={"Authorization": f"Bearer {token}"},
        json={"tenant_id": other},
    )
    assert switched.status_code == 200
    assert switched.json()["user"]["tenant_id"] == other
    assert switched.json()["user"]["role"] == "HOTEL_MANAGER"

    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": other},
    )
    assert me.status_code == 200
    assert me.json()["user"]["tenant_id"] == other
    assert me.json()["user"]["role"] == "HOTEL_MANAGER"


def test_me_survives_active_property_other_than_home(client):
    """Chrome refresh must not 404 /auth/me when X-Property-ID ≠ home tenant.

    webclaw_user.tenant_id is the home hotel; auto-scoping it to the active
    branch made owners look "logged out" after reload.
    """
    from db import get_connection
    from tenant import upsert_membership

    conn = get_connection()
    home = str(uuid.uuid4())
    other = str(uuid.uuid4())
    _user(conn, "owner@multi.test", "HotelPass1", "Hotel Owner", tenant_id=home)
    user = conn.execute(
        "SELECT id FROM webclaw_user WHERE email = ?", ("owner@multi.test",)
    ).fetchone()
    upsert_membership(
        conn, user["id"], home,
        role_code="HOTEL_OWNER", role_name="Hotel Owner", hotel_name="Home",
    )
    upsert_membership(
        conn, user["id"], other,
        role_code="HOTEL_OWNER", role_name="Hotel Owner", hotel_name="Branch",
    )
    conn.commit()

    login = client.post("/api/v1/auth/login", json={
        "email": "owner@multi.test", "password": "HotelPass1",
    })
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = client.get(
        "/api/v1/auth/me",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Property-ID": other,
            "X-Tenant-Id": other,
        },
    )
    assert me.status_code == 200, me.text
    body = me.json()["user"]
    assert body["user_id"] == user["id"]
    assert body["email"] == "owner@multi.test"
    assert body["active_property_id"] == other


def test_superadmin_login_role(client):
    from db import get_connection
    conn = get_connection()
    _user(conn, "root@pms.test", "HotelPass1", "System Manager", tenant_id=None)
    resp = client.post("/api/v1/auth/login", json={
        "email": "root@pms.test", "password": "HotelPass1",
    })
    assert resp.status_code == 200
    user = resp.json()["user"]
    assert user["role"] == "SUPER_ADMIN"
    assert user["is_superadmin"] is True
    assert user["tenant_id"] is None


def test_sql_rewriter_scopes_webclaw_user():
    from sql_tenant import apply_tenant_predicate

    sql, params = apply_tenant_predicate(
        "SELECT email FROM webclaw_user WHERE status = ?",
        ["active"],
        "hotel-a",
        lambda table: "tenant_id" if table == "webclaw_user" else None,
    )
    assert "webclaw_user.tenant_id = ?" in sql
    assert params == ["active", "hotel-a"]
