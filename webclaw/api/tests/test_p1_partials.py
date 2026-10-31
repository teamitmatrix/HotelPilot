"""P1 remaining partials — kill switches, usage meters, ops controls, backups."""
from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

import db
from auth.passwords import hash_password
from admin_growth import (
    ensure_growth_tables,
    is_kill_switch_on,
    record_usage,
    assert_within_plan_limits,
)
from admin_ops import (
    build_tenant_export_package,
    ensure_ops_tables,
    is_job_paused,
    notify_ops,
)
from services.channel_manager import push_outbound


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


def _seed_hotel(*, name="P1 Inn") -> str:
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
    conn.commit()
    return tid


def _login(client) -> dict:
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "AdminPass1!"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_kill_switch_blocks_channel_push(setup_dbs):
    tid = _seed_hotel()
    conn = db.get_connection()
    ensure_growth_tables(conn)
    conn.execute(
        "UPDATE webclaw_feature_flag SET enabled_global = 1 WHERE key = 'channel_manager_kill'"
    )
    conn.commit()
    assert is_kill_switch_on(tid, "channel_manager_kill") is True
    result = push_outbound(tid, reason="test")
    assert result.get("skipped") == "kill_switch"
    assert result.get("pushed") == []


def test_kill_switch_ai_chat_returns_503(client, setup_dbs):
    _seed_admin()
    tid = _seed_hotel()
    conn = db.get_connection()
    ensure_growth_tables(conn)
    conn.execute(
        "UPDATE webclaw_feature_flag SET enabled_global = 1 WHERE key = 'ai_chat_kill'"
    )
    conn.commit()
    h = _login(client)
    with patch("tenant.get_tenant_context") as mock_ctx:
        mock_ctx.return_value = type(
            "T",
            (),
            {
                "tenant_id": tid,
                "is_superadmin": False,
                "impersonating": False,
                "scope_all_properties": False,
                "active_property_id": None,
                "group_id": None,
                "roles": ["owner"],
                "assigned_properties": (),
            },
        )()
        r = client.post(
            "/api/v1/chat/stream",
            headers=h,
            json={"message": "hello"},
        )
    assert r.status_code == 503
    assert r.json().get("code") == "FEATURE_DISABLED"


def test_record_usage_and_meter_caps(setup_dbs):
    tid = _seed_hotel()
    # Starter api_calls cap is 10000 — force tiny cap via recording past it
    for _ in range(3):
        record_usage(tid, "api_calls", 1)
    # Under default starter cap
    assert assert_within_plan_limits(tid, "api_calls", delta=1) is None

    # Exhaust by recording a huge delta then assert
    record_usage(tid, "api_calls", 100000)
    err = assert_within_plan_limits(tid, "api_calls", delta=1)
    assert err is not None
    assert err["code"] == "PLAN_LIMIT_EXCEEDED"
    assert err["metric"] == "api_calls"


def test_usage_overview_includes_meters(client, setup_dbs):
    _seed_admin()
    tid = _seed_hotel()
    record_usage(tid, "ai_tokens", 42)
    h = _login(client)
    r = client.get("/api/v1/admin/usage/overview", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()["usage"]
    match = next((u for u in rows if u["tenant_id"] == tid), None)
    assert match is not None
    assert "api_calls" in match
    assert "ai_tokens" in match
    assert "storage_gb" in match
    assert match["ai_tokens"] >= 42


def test_ops_jobs_pause_and_alerts(client, setup_dbs):
    _seed_admin()
    h = _login(client)
    conn = db.get_connection()
    ensure_ops_tables(conn)
    assert is_job_paused("night_audit") is False

    r = client.get("/api/v1/admin/ops/health", headers=h)
    assert r.status_code == 200, r.text
    assert "jobs" in r.json()["health"]

    r = client.put(
        "/api/v1/admin/ops/jobs/night_audit",
        headers=h,
        json={"is_paused": True},
    )
    assert r.status_code == 200, r.text
    assert is_job_paused("night_audit") is True

    aid = notify_ops("test_alert", {"x": 1}, severity="warning", title="Test alert")
    r = client.get("/api/v1/admin/ops/alerts", headers=h)
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()["alerts"]]
    assert aid in ids

    r = client.post(f"/api/v1/admin/ops/alerts/{aid}/ack", headers=h)
    assert r.status_code == 200


def test_offsite_backup_policy_fields(client, setup_dbs):
    _seed_admin()
    h = _login(client)
    r = client.get("/api/v1/admin/security-policy", headers=h)
    assert r.status_code == 200
    policy = r.json()["policy"]
    assert "offsite_backup_enabled" in policy
    assert "offsite_backup_url" in policy
    assert policy.get("offsite_backup_token") == ""

    r = client.put(
        "/api/v1/admin/security-policy",
        headers=h,
        json={
            "offsite_backup_enabled": True,
            "offsite_backup_url": "https://backup.example/put",
            "offsite_backup_token": "secret-token",
        },
    )
    assert r.status_code == 200, r.text
    saved = r.json()["policy"]
    assert saved["offsite_backup_enabled"] is True
    assert saved["offsite_backup_url"] == "https://backup.example/put"
    assert saved["offsite_backup_token"] == ""
    assert saved["offsite_backup_token_set"] is True


def test_tenant_export_package(client, setup_dbs):
    _seed_admin()
    tid = _seed_hotel(name="Export Me")
    pkg = build_tenant_export_package(tid)
    assert pkg["hotel"] is not None
    assert pkg["tenant_id"] == tid

    h = _login(client)
    r = client.get(f"/api/v1/admin/tenants/{tid}/export", headers=h)
    assert r.status_code == 200
    assert "application/json" in r.headers.get("content-type", "")
    body = r.json()
    assert body["tenant_id"] == tid
    assert body["hotel"] is not None
