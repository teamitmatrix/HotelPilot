"""Group → Property hierarchy: owner branch API + ALL scoping."""
from __future__ import annotations

import os
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pgutil import connect_web, connect_erp

from auth.passwords import hash_password
from auth.jwt_utils import create_access_token, get_signing_secret
from init_webclaw_db import init_tables
from property_scope import (
    attach_property_to_group,
    create_group,
    ensure_group_schema_erp,
    ensure_group_schema_webclaw,
)
from rbac import HOTEL_OWNER, seed_rbac
from sql_tenant import apply_multi_property_predicate, apply_tenant_predicate
from tenant import (
    TenantIsolationError,
    bind_request_tenant,
    build_context,
    enforce_payload_tenant,
    upsert_membership,
)



def _seed_owner(paths):
    prop_a = str(uuid.uuid4())
    prop_b = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    web = connect_web()
    erp = connect_erp()
    role = web.execute(
        "SELECT id FROM webclaw_role WHERE code = ?", (HOTEL_OWNER,)
    ).fetchone()[0]
    gid = create_group(erp, name="Acme Group", admin_email="owner@acme.test")
    for pid, name in ((prop_a, "Downtown"), (prop_b, "Airport")):
        erp.execute(
            "INSERT INTO company (id, name, group_id) VALUES (?, ?, ?)",
            (pid, name, gid),
        )
        erp.execute(
            """INSERT INTO pms_tenant
               (id, company_id, hotel_name, admin_email, timezone, currency, country, status, group_id)
               VALUES (?, ?, ?, 'owner@acme.test', 'UTC', 'USD', 'US', 'active', ?)""",
            (pid, pid, name, gid),
        )
        attach_property_to_group(erp, pid, gid)

    web.execute(
        """INSERT INTO webclaw_user
           (id, username, email, full_name, password_hash, status, tenant_id, group_id)
           VALUES (?, 'owner', 'owner@acme.test', 'Owner', ?, 'active', ?, ?)""",
        (user_id, hash_password("Password1!"), prop_a, gid),
    )
    web.execute(
        "INSERT INTO webclaw_user_role (id, user_id, role_id, tenant_id) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, role, prop_a),
    )
    for pid, name in ((prop_a, "Downtown"), (prop_b, "Airport")):
        upsert_membership(
            web,
            user_id,
            pid,
            role_code=HOTEL_OWNER,
            role_name="Hotel Owner",
            hotel_name=name,
        )
    web.commit()
    erp.commit()
    secret = get_signing_secret(web)
    token = create_access_token(
        user_id,
        "owner@acme.test",
        secret,
        tenant_id=prop_a,
        group_id=gid,
        assigned_properties=[prop_a, prop_b],
        active_property_id=prop_a,
    )
    web.close()
    erp.close()
    return {
        "user_id": user_id,
        "token": token,
        "group_id": gid,
        "prop_a": prop_a,
        "prop_b": prop_b,
    }


@pytest.fixture
def client(setup_test_db):
    from main import app

    return TestClient(app)


def test_list_owner_properties_includes_hotel_address(setup_test_db):
    """Branches list must surface hospitalityclaw_hotel.address (settings source)."""
    from property_scope import list_properties_in_group

    seeded = _seed_owner(setup_test_db)
    erp = connect_erp()
    erp.execute(
        """CREATE TABLE IF NOT EXISTS hospitalityclaw_hotel (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT,
            timezone TEXT,
            currency TEXT,
            country TEXT,
            address TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    erp.execute(
        """INSERT INTO hospitalityclaw_hotel
           (id, tenant_id, name, address) VALUES (?, ?, 'Downtown', '6th Rd St')""",
        (str(uuid.uuid4()), seeded["prop_a"]),
    )
    erp.commit()
    props = list_properties_in_group(erp, seeded["group_id"])
    erp.close()
    by_id = {p["property_id"]: p for p in props}
    assert by_id[seeded["prop_a"]]["address"] == "6th Rd St"
    assert by_id[seeded["prop_b"]].get("address") in (None, "")


def test_list_owner_properties(client, setup_test_db):
    seeded = _seed_owner(setup_test_db)
    res = client.get(
        "/api/v1/owner/properties",
        headers={"Authorization": f"Bearer {seeded['token']}"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok"
    assert body["group_id"] == seeded["group_id"]
    ids = {p["property_id"] for p in body["properties"]}
    assert seeded["prop_a"] in ids
    assert seeded["prop_b"] in ids


def test_owner_can_close_branch_for_bookings(client, setup_test_db, monkeypatch):
    seeded = _seed_owner(setup_test_db)
    pushes = []

    def fake_push(tenant_id, **kwargs):
        pushes.append({"tenant_id": tenant_id, **kwargs})
        return {"status": "ok", "pushed": []}

    monkeypatch.setattr("services.channel_manager.push_outbound", fake_push)

    close = client.patch(
        f"/api/v1/owner/properties/{seeded['prop_b']}/status",
        headers={"Authorization": f"Bearer {seeded['token']}"},
        json={"status": "closed"},
    )
    assert close.status_code == 200, close.text
    body = close.json()
    assert body["status"] == "ok"
    assert body["property"]["status"] == "closed"
    assert body["property"]["accepting_bookings"] is False
    assert pushes and pushes[0]["reason"] == "branch-closed"

    listed = client.get(
        "/api/v1/owner/properties",
        headers={"Authorization": f"Bearer {seeded['token']}"},
    ).json()
    by_id = {p["property_id"]: p for p in listed["properties"]}
    assert by_id[seeded["prop_b"]]["status"] == "closed"
    assert by_id[seeded["prop_a"]]["status"] == "active"

    from property_scope import is_accepting_bookings
    erp = connect_erp()
    assert is_accepting_bookings(erp, seeded["prop_b"]) is False
    assert is_accepting_bookings(erp, seeded["prop_a"]) is True
    erp.close()

    reopen = client.patch(
        f"/api/v1/owner/properties/{seeded['prop_b']}/status",
        headers={"Authorization": f"Bearer {seeded['token']}"},
        json={"status": "active"},
    )
    assert reopen.status_code == 200, reopen.text
    assert reopen.json()["property"]["status"] == "active"
    assert pushes[-1]["reason"] == "branch-reopened"


def test_x_property_all_scopes_reads(setup_test_db):
    seeded = _seed_owner(setup_test_db)
    web = connect_web()
    ctx = build_context(web, seeded["user_id"], "owner@acme.test")
    all_ctx = bind_request_tenant(web, ctx, None, property_header="ALL")
    assert all_ctx.scope_all_properties is True
    assert all_ctx.active_property_id == "ALL"
    assert all_ctx.isolation_id is None
    assert set(all_ctx.assigned_properties) >= {seeded["prop_a"], seeded["prop_b"]}

    with pytest.raises(TenantIsolationError):
        enforce_payload_tenant(
            all_ctx,
            {"_action": "add-reservation", "guest-name": "Ada"},
        )
    bound = enforce_payload_tenant(all_ctx, {"_action": "list-reservations"})
    # Group aggregation is env-only (ERPCLAW_GROUP_ID / ERPCLAW_TENANT_IDS);
    # CLI --group-id is rejected by skill argparse.
    assert "group-id" not in bound
    assert "property-ids" not in bound
    assert all_ctx.group_id == seeded["group_id"]
    web.close()


def test_multi_property_sql_in_clause():
    sql = "SELECT * FROM hospitalityclaw_reservation WHERE status = ?"
    new_sql, params = apply_multi_property_predicate(
        sql, ["open"], ["p1", "p2"], lambda t: "tenant_id"
    )
    assert "tenant_id IN (?, ?)" in new_sql
    # Existing ? placeholders stay in place; IN values are appended after them.
    assert params == ["open", "p1", "p2"]


def test_multi_property_prefers_group_id():
    sql = "SELECT * FROM company"
    new_sql, params = apply_multi_property_predicate(
        sql,
        None,
        ["p1", "p2"],
        lambda t: "id",
        group_id="g1",
        has_group_column=lambda t: True,
    )
    assert "group_id = ?" in new_sql
    assert params == ("g1",)


def test_single_property_still_equals():
    sql = "SELECT * FROM company"
    new_sql, params = apply_tenant_predicate(sql, None, "p1", lambda t: "id")
    assert "id = ?" in new_sql
    assert params == ("p1",)
