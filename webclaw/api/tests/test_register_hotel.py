"""Self-service hotel registration — public /auth/register-hotel."""
import os
import sys
import uuid

import jwt
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pgutil import connect_web, connect_erp

from fastapi.testclient import TestClient
from auth.passwords import hash_password
from auth.jwt_utils import ALGORITHM



@pytest.fixture
def client(setup_dbs):
    from main import app
    return TestClient(app)


def test_register_hotel_validation(client):
    resp = client.post("/api/v1/auth/register-hotel", json={
        "hotel_name": "X",
        "admin_email": "not-an-email",
        "password": "short",
    })
    assert resp.status_code == 400


def test_register_hotel_provisions_owner(client, setup_dbs, monkeypatch):
    captured = {}

    async def fake_execute(skill, action, params, timeout=None, tenant_id=None, is_superadmin=False):
        captured.setdefault("calls", []).append({"skill": skill, "action": action, "params": params})
        if action == "setup-company":
            captured["company_id"] = params.get("company-id")
            captured["name"] = params.get("name")
            captured["company_currency"] = params.get("currency")
            return {"status": "ok", "company_id": params.get("company-id")}
        if action == "hospitality-seed-defaults":
            captured["hotel_currency"] = params.get("currency")
            captured["hotel_name"] = params.get("hotel-name")
        return {"status": "ok"}

    monkeypatch.setattr("auth.onboarding.execute_skill", fake_execute)

    resp = client.post("/api/v1/auth/register-hotel", json={
        "hotel_name": "Harbor Inn",
        "admin_email": "owner@harbor.test",
        "password": "HarborPass1",
        "country": "United Arab Emirates",
        "currency": "AED",
        "timezone": "Asia/Dubai",
        "plan_code": "business",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ok"
    assert data["access_token"]
    assert data["user"]["email"] == "owner@harbor.test"
    assert data["user"]["tenant_id"]
    assert data["user"]["is_superadmin"] is False
    assert data["user"]["roles"][0]["role_name"] == "Hotel Owner"
    assert data["tenant"]["plan_code"] == "business"
    assert captured["company_id"] == data["tenant"]["tenant_id"]
    assert captured["name"] == "Harbor Inn"
    assert captured["company_currency"] == "AED"
    assert captured["hotel_currency"] == "AED"
    assert data["tenant"]["currency"] == "AED"

    payload = jwt.decode(
        data["access_token"],
        options={"verify_signature": False},
        algorithms=["HS256"],
    )
    assert payload["tenant_id"] == data["tenant"]["tenant_id"]
    assert payload["is_superadmin"] is False

    web = connect_web()
    user = web.execute("SELECT * FROM webclaw_user WHERE email = ?", ("owner@harbor.test",)).fetchone()
    assert user["tenant_id"] == data["tenant"]["tenant_id"]
    role = web.execute(
        """SELECT r.name FROM webclaw_user_role ur
           JOIN webclaw_role r ON r.id = ur.role_id WHERE ur.user_id = ?""",
        (user["id"],),
    ).fetchone()
    assert role["name"] == "Hotel Owner"
    web.close()

    # Duplicate email rejected
    again = client.post("/api/v1/auth/register-hotel", json={
        "hotel_name": "Harbor Inn 2",
        "admin_email": "owner@harbor.test",
        "password": "HarborPass1",
    })
    assert again.status_code == 409


def test_public_plans_catalog(client, setup_dbs):
    resp = client.get("/api/v1/auth/plans")
    assert resp.status_code == 200
    data = resp.json()
    codes = {p["code"] for p in data["plans"]}
    assert {"starter", "business", "enterprise"} <= codes
    assert len(data["feature_catalog"]) >= 5
