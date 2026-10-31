"""P3 partials — integrations health, white-label DNS/runtime, SSO/SCIM, DSAR erasure."""
from __future__ import annotations

import json
import os
import sys
import uuid
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

import db
from auth.passwords import hash_password


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


def _login(client, email="admin@test.com", password="AdminPass1!") -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_integrations_health_endpoint(client, setup_dbs):
    _seed_admin()
    h = _login(client)
    r = client.get("/api/v1/admin/integrations/health", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert "sendgrid" in body["providers"]
    assert "stripe" in body["providers"]
    assert "recent_deliveries" in body


def test_white_label_dns_and_public_branding(client, setup_dbs, monkeypatch):
    _seed_admin()
    h = _login(client)
    tid = str(uuid.uuid4())
    monkeypatch.setenv("WEBCLAW_BRANDING_DNS_TARGET", "app.hotelpilot.test")

    r = client.put(
        f"/api/v1/admin/white-label/{tid}",
        headers=h,
        json={
            "brand_name": "Acme Stay",
            "logo_url": "https://cdn.example/logo.png",
            "primary_color": "#0a7",
            "custom_domain": "book.acme.test",
            "email_from_name": "Acme",
            "email_from_address": "hello@acme.test",
            "support_url": "https://acme.test/help",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["branding"]["brand_name"] == "Acme Stay"

    fake_ip = ("127.0.0.1", 0)

    def fake_getaddrinfo(host, *args, **kwargs):
        return [(None, None, None, None, fake_ip)]

    def fake_gethostbyname_ex(host):
        return ("app.hotelpilot.test", [], ["127.0.0.1"])

    with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo), patch(
        "socket.gethostbyname_ex", side_effect=fake_gethostbyname_ex
    ):
        r = client.post(f"/api/v1/admin/white-label/{tid}/verify-dns", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["dns"]["dns_verified"] is True

    r = client.get(f"/api/v1/admin/public/branding?tenant_id={tid}")
    assert r.status_code == 200, r.text
    brand = r.json()["branding"]
    assert brand["brand_name"] == "Acme Stay"
    assert brand["logo_url"]

    r = client.get(
        "/api/v1/admin/public/branding",
        headers={"Host": "book.acme.test"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["branding"]["tenant_id"] == tid


def test_sso_test_mode_login(client, setup_dbs, monkeypatch):
    monkeypatch.setenv("WEBCLAW_SSO_TEST_MODE", "1")
    _seed_admin()
    h = _login(client)
    tid = str(uuid.uuid4())

    r = client.put(
        f"/api/v1/admin/identity/{tid}",
        headers=h,
        json={
            "sso_enabled": True,
            "sso_protocol": "oidc",
            "issuer_url": "test://idp",
            "client_id": "webclaw",
        },
    )
    assert r.status_code == 200, r.text

    r = client.get(f"/api/v1/auth/sso/{tid}/start")
    assert r.status_code == 200, r.text
    assert r.json().get("test_mode") is True
    authorize = r.json()["authorize_url"]
    assert "state=" in authorize

    # Follow callback as JSON API client
    from urllib.parse import urlparse, parse_qs

    qs = parse_qs(urlparse(authorize).query)
    state = qs["state"][0]
    r = client.get(
        "/api/v1/auth/sso/callback",
        params={"code": "sso_test", "state": state, "email": "sso.user@hotel.test", "format": "json"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["access_token"]
    assert body["email"] == "sso.user@hotel.test"


def test_scim_users_crud(client, setup_dbs):
    _seed_admin()
    h = _login(client)
    tid = str(uuid.uuid4())

    r = client.put(
        f"/api/v1/admin/identity/{tid}",
        headers=h,
        json={"scim_enabled": True, "sso_enabled": False},
    )
    assert r.status_code == 200, r.text
    identity = r.json()["identity"]
    assert identity.get("scim_token_revealed") is True
    token = identity["scim_bearer_token"]
    assert token and "•" not in token

    scim_h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = client.post(
        f"/api/v1/scim/v2/{tid}/Users",
        headers=scim_h,
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "scim.staff@hotel.test",
            "displayName": "SCIM Staff",
            "emails": [{"value": "scim.staff@hotel.test", "primary": True}],
            "active": True,
        },
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["id"]

    r = client.get(f"/api/v1/scim/v2/{tid}/Users", headers=scim_h)
    assert r.status_code == 200, r.text
    assert r.json()["totalResults"] >= 1

    r = client.patch(
        f"/api/v1/scim/v2/{tid}/Users/{user_id}",
        headers=scim_h,
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "Replace", "path": "active", "value": False}],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["active"] is False


def test_dsar_export_and_erasure(client, setup_dbs):
    _seed_admin()
    h = _login(client)
    tid = str(uuid.uuid4())
    subject = "subject.dsar@hotel.test"

    conn = db.get_connection()
    uid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO webclaw_user (id, username, email, full_name, password_hash, status) "
        "VALUES (?, ?, ?, ?, ?, 'active')",
        (uid, "subject", subject, "Subject Person", hash_password("SubjectPass1!")),
    )
    conn.commit()

    r = client.post(
        "/api/v1/admin/compliance/dsar",
        headers=h,
        json={
            "tenant_id": tid,
            "request_type": "export",
            "subject_email": subject,
            "notes": "export me",
        },
    )
    assert r.status_code == 200, r.text
    export_id = r.json()["id"]

    r = client.get(f"/api/v1/admin/compliance/dsar/{export_id}/export", headers=h)
    assert r.status_code == 200, r.text
    package = r.json()
    assert package.get("subject_email") == subject or subject in json.dumps(package)

    r = client.post(
        "/api/v1/admin/compliance/dsar",
        headers=h,
        json={
            "tenant_id": tid,
            "request_type": "erasure",
            "subject_email": subject,
        },
    )
    assert r.status_code == 200, r.text
    erasure_id = r.json()["id"]

    r = client.post(
        f"/api/v1/admin/compliance/dsar/{erasure_id}/execute-erasure",
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"

    row = conn.execute(
        "SELECT email, status, full_name FROM webclaw_user WHERE id = ?", (uid,)
    ).fetchone()
    email = row[0] if isinstance(row, tuple) else row["email"]
    status = row[1] if isinstance(row, tuple) else row["status"]
    # Erased/anonymized — email should no longer be the clear subject address
    assert email != subject or status in ("deleted", "disabled", "anonymized")
