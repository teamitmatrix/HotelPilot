"""Auth API tests — login, refresh, logout, me, change-password, RBAC.

Uses Postgres via ``tests/conftest.py`` (database ``pms_test``).
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient


@pytest.fixture
def client(setup_test_db):
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


@pytest.fixture
def seeded_user(setup_test_db):
    from auth.passwords import hash_password
    from db import get_connection

    conn = get_connection()
    user_id = str(uuid.uuid4())
    pw_hash = hash_password("TestPass123!")
    conn.execute(
        "INSERT INTO webclaw_user (id, username, email, full_name, password_hash, status) "
        "VALUES (?, ?, ?, ?, ?, 'active')",
        (user_id, "testadmin", "admin@test.com", "Test Admin", pw_hash),
    )
    role_id = _role_id(conn, "System Manager", is_system=1)
    conn.execute(
        "INSERT INTO webclaw_user_role (id, user_id, role_id) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), user_id, role_id),
    )
    conn.commit()
    return {"user_id": user_id, "email": "admin@test.com", "password": "TestPass123!"}


@pytest.fixture
def limited_user(setup_test_db):
    from auth.passwords import hash_password
    from db import get_connection

    conn = get_connection()
    user_id = str(uuid.uuid4())
    pw_hash = hash_password("LimitedPass1!")
    conn.execute(
        "INSERT INTO webclaw_user (id, username, email, full_name, password_hash, status) "
        "VALUES (?, ?, ?, ?, ?, 'active')",
        (user_id, "limiteduser", "limited@test.com", "Limited User", pw_hash),
    )
    role_id = _role_id(conn, "Accounts User", is_system=0)
    conn.execute(
        "INSERT INTO webclaw_user_role (id, user_id, role_id) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), user_id, role_id),
    )
    conn.execute(
        "INSERT INTO webclaw_role_permission (id, role_id, skill, action_pattern, allowed) "
        "VALUES (?, ?, 'erpclaw', 'list-*', 1)",
        (str(uuid.uuid4()), role_id),
    )
    conn.commit()
    return {"user_id": user_id, "email": "limited@test.com", "password": "LimitedPass1!"}


def _login(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


def test_login_valid_credentials(client, seeded_user):
    resp = _login(client, seeded_user["email"], seeded_user["password"])
    data = resp.json()
    assert data["status"] == "ok"
    assert "access_token" in data
    assert data["user"]["email"] == seeded_user["email"]
    assert "refresh_token" in resp.cookies


def test_login_invalid_password(client, seeded_user):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user["email"], "password": "WrongPass1!"},
    )
    assert resp.status_code in (401, 403)


def test_login_unknown_user(client, setup_test_db):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@test.com", "password": "Whatever1!"},
    )
    assert resp.status_code in (401, 403)


def test_me_requires_auth(client, setup_test_db):
    assert client.get("/api/v1/auth/me").status_code in (401, 403)


def test_me_with_token(client, seeded_user):
    token = _login(client, seeded_user["email"], seeded_user["password"]).json()["access_token"]
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == seeded_user["email"]


def test_refresh_rotates_cookie(client, seeded_user):
    login = _login(client, seeded_user["email"], seeded_user["password"])
    refresh = client.post("/api/v1/auth/refresh")
    assert refresh.status_code == 200, refresh.text
    assert "access_token" in refresh.json()
    assert "refresh_token" in refresh.cookies


def test_logout_clears_session(client, seeded_user):
    _login(client, seeded_user["email"], seeded_user["password"])
    out = client.post("/api/v1/auth/logout")
    assert out.status_code == 200
    assert client.post("/api/v1/auth/refresh").status_code in (401, 403)


def test_change_password(client, seeded_user):
    token = _login(client, seeded_user["email"], seeded_user["password"]).json()["access_token"]
    resp = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": seeded_user["password"], "new_password": "NewPass123!"},
    )
    assert resp.status_code == 200, resp.text
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": seeded_user["email"], "password": "NewPass123!"},
        ).status_code
        == 200
    )


def test_check_setup(client, setup_test_db):
    resp = client.get("/api/v1/auth/check-setup")
    assert resp.status_code == 200
    assert "setup_required" in resp.json() or "status" in resp.json()


def test_limited_user_can_login(client, limited_user):
    resp = _login(client, limited_user["email"], limited_user["password"])
    assert resp.json()["user"]["email"] == limited_user["email"]
