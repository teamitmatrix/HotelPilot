"""API / webhook platform admin (#25)."""
from __future__ import annotations

import io
import os
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

import db
from auth.passwords import hash_password
from admin_api_platform import (
    deliver_webhook,
    ensure_api_platform_tables,
    lookup_api_key,
    _hash_key,
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
    return {"email": "admin@test.com", "password": "AdminPass1!"}


def _login(client, email, password) -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_create_revoke_and_lookup_api_key(client, setup_dbs):
    _seed_admin()
    token = _login(client, "admin@test.com", "AdminPass1!")
    h = _headers(token)

    r = client.post(
        "/api/v1/admin/api-platform/keys",
        headers=h,
        json={"name": "Partner A", "rate_limit_per_min": 120, "scopes": ["read", "write"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["secret"].startswith("hp_live_")
    key_id = body["key"]["id"]
    secret = body["secret"]
    assert body["key"]["key_prefix"] == secret[:16]
    assert body["key"]["rate_limit_per_min"] == 120

    conn = db.get_connection()
    found = lookup_api_key(conn, secret)
    assert found is not None
    assert found["id"] == key_id
    assert lookup_api_key(conn, "hp_live_bogus") is None

    r = client.post(f"/api/v1/admin/api-platform/keys/{key_id}/revoke", headers=h)
    assert r.status_code == 200
    assert lookup_api_key(conn, secret) is None

    r = client.get("/api/v1/admin/api-platform/keys", headers=h)
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert any(k["id"] == key_id and k["revoked_at"] for k in keys)


def test_rate_limit_policy(client, setup_dbs):
    _seed_admin()
    token = _login(client, "admin@test.com", "AdminPass1!")
    h = _headers(token)

    r = client.get("/api/v1/admin/api-platform/rate-limits", headers=h)
    assert r.status_code == 200
    assert r.json()["policy"]["default_per_min"] == 60

    r = client.put(
        "/api/v1/admin/api-platform/rate-limits",
        headers=h,
        json={"default_per_min": 90, "burst_per_min": 180},
    )
    assert r.status_code == 200
    assert r.json()["policy"]["default_per_min"] == 90
    assert r.json()["policy"]["burst_per_min"] == 180


def test_webhook_crud_test_and_delivery_log(client, setup_dbs):
    _seed_admin()
    token = _login(client, "admin@test.com", "AdminPass1!")
    h = _headers(token)

    r = client.post(
        "/api/v1/admin/api-platform/webhooks",
        headers=h,
        json={
            "name": "Ops hook",
            "url": "https://example.com/hooks/hotelpilot",
            "events": ["booking.created", "test.ping"],
        },
    )
    assert r.status_code == 200, r.text
    wid = r.json()["webhook"]["id"]
    assert r.json()["secret"]
    assert r.json()["webhook"]["secret"].startswith("•") or "•" in r.json()["webhook"]["secret"]

    class FakeResp:
        status = 200

        def read(self):
            return b'{"ok":true}'

        def getcode(self):
            return 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("urllib.request.urlopen", return_value=FakeResp()):
        r = client.post(f"/api/v1/admin/api-platform/webhooks/{wid}/test", headers=h)
    assert r.status_code == 200, r.text
    delivery = r.json()["delivery"]
    assert delivery["ok"] is True
    assert delivery["status"] == "success"
    assert delivery["http_status"] == 200

    r = client.get("/api/v1/admin/api-platform/deliveries", headers=h)
    assert r.status_code == 200
    deliveries = r.json()["deliveries"]
    assert len(deliveries) >= 1
    assert deliveries[0]["endpoint_id"] == wid
    assert deliveries[0]["event_type"] == "test.ping"

    r = client.get("/api/v1/admin/api-platform/overview", headers=h)
    assert r.status_code == 200
    ov = r.json()["overview"]
    assert ov["webhooks_active"] >= 1
    assert ov["keys_total"] >= 0

    r = client.delete(f"/api/v1/admin/api-platform/webhooks/{wid}", headers=h)
    assert r.status_code == 200


def test_webhook_delivery_failure_increments_count(setup_dbs):
    conn = db.get_connection()
    ensure_api_platform_tables(conn)
    wid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO webclaw_webhook_endpoint
           (id, tenant_id, name, url, secret, events, is_active, failure_count,
            last_success_at, last_failure_at, created_at, updated_at)
           VALUES (?, NULL, 'Fail hook', 'https://example.com/fail', 'sec', '["*"]',
                   1, 0, NULL, NULL, ?, ?)""",
        (wid, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.commit()

    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        result = deliver_webhook(conn, wid, event_type="test.ping")
    assert result["ok"] is False
    assert result["status"] == "failed"
    row = conn.execute(
        "SELECT failure_count FROM webclaw_webhook_endpoint WHERE id = ?", (wid,)
    ).fetchone()
    assert int(row[0]) == 1


def test_key_hash_is_not_reversible_from_list(client, setup_dbs):
    _seed_admin()
    token = _login(client, "admin@test.com", "AdminPass1!")
    h = _headers(token)
    r = client.post(
        "/api/v1/admin/api-platform/keys",
        headers=h,
        json={"name": "Secret key"},
    )
    secret = r.json()["secret"]
    key_id = r.json()["key"]["id"]

    r = client.get("/api/v1/admin/api-platform/keys", headers=h)
    listed = next(k for k in r.json()["keys"] if k["id"] == key_id)
    assert "secret" not in listed
    assert listed["key_prefix"] != secret
    assert secret.startswith(listed["key_prefix"])
    assert _hash_key(secret) != listed["key_prefix"]
