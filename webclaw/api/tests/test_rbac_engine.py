"""PMS RBAC engine: role codes, named permissions, tenant-scoped guard."""
import os
import sys
import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pgutil import connect_web, connect_erp

from init_webclaw_db import init_tables
from rbac import (
    ACCOUNTANT,
    FRONT_DESK,
    HOUSEKEEPING,
    HOTEL_MANAGER,
    ROLE_DISPLAY,
    ROLE_PERMISSIONS,
    SUPER_ADMIN,
    check_permission,
    has_permission,
    require_permission,
    seed_rbac,
)
from tenant import TenantContext, reset_tenant_context, set_tenant_context
from auth.passwords import hash_password


@pytest.fixture
def conn(setup_test_db, monkeypatch):
    """Platform DB on Postgres (webclaw schema), truncated per test."""
    c = connect_web()
    # init_tables already run by setup_test_db; ensure RBAC seed present.
    seed_rbac(c)
    c.commit()
    try:
        yield c
    finally:
        # PostgreSQL SELECTs start a transaction. Leaving this connection open
        # makes the next test's TRUNCATE wait indefinitely for its table locks.
        try:
            c.rollback()
        finally:
            c.close()


def _role_id(conn, code):
    return conn.execute("SELECT id FROM webclaw_role WHERE code = ?", (code,)).fetchone()[0]


def _user(conn, email, code, tenant_id=None):
    user_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO webclaw_user (id, username, email, full_name, password_hash, status, tenant_id)
           VALUES (?, ?, ?, ?, ?, 'active', ?)""",
        (user_id, email.split("@")[0], email, email, hash_password("Passw0rd!"), tenant_id),
    )
    conn.execute(
        "INSERT INTO webclaw_user_role (id, user_id, role_id, tenant_id) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, _role_id(conn, code), tenant_id),
    )
    conn.commit()
    return user_id


def test_membership_grant_covers_second_hotel(conn):
    hotel_a = str(uuid.uuid4())
    hotel_b = str(uuid.uuid4())
    user_id = _user(conn, "region@hotels.test", HOTEL_MANAGER, tenant_id=hotel_a)
    conn.execute(
        """INSERT INTO webclaw_user_tenant
           (id, user_id, tenant_id, role_code, role_name, hotel_name)
           VALUES (?, ?, ?, 'HOTEL_MANAGER', 'Hotel Manager', 'North Inn')""",
        (str(uuid.uuid4()), user_id, hotel_b),
    )
    conn.commit()
    assert has_permission(conn, user_id, "bookings.write", tenant_id=hotel_a) is True
    assert has_permission(conn, user_id, "bookings.write", tenant_id=hotel_b) is True
    assert has_permission(conn, user_id, "bookings.write", tenant_id=str(uuid.uuid4())) is False


def test_hotel_roles_seeded(conn):
    rows = conn.execute(
        "SELECT code FROM webclaw_role WHERE code IS NOT NULL ORDER BY code"
    ).fetchall()
    codes = {r[0] for r in rows}
    assert codes >= {
        "SUPER_ADMIN", "HOTEL_OWNER", "HOTEL_ADMIN", "GENERAL_MANAGER",
        "HOTEL_MANAGER", "FRONT_DESK", "HOUSEKEEPING", "HOTEL_ACCOUNTANT",
        "ACCOUNTANT",
    }
    views = {
        r[0]
        for r in conn.execute(
            """
            SELECT table_name FROM information_schema.views
            WHERE table_schema = 'webclaw'
            """
        ).fetchall()
    }
    assert {"roles", "permissions", "user_roles"} <= views


def test_front_desk_default_grants(conn):
    grants = set(ROLE_PERMISSIONS[FRONT_DESK])
    assert grants == {
        "bookings.read", "bookings.write",
        "guests.read", "guests.write",
        "reservations.check_in", "reservations.check_out",
        "rooms.read",
        "folios.read", "folios.write",
    }
    assert "rooms.update_status" not in grants
    assert "ledger.read" not in grants
    assert "rooms.write" not in grants


def test_housekeeping_only_room_status(conn):
    assert ROLE_PERMISSIONS[HOUSEKEEPING] == ("rooms.read", "rooms.update_status")


def test_accountant_ledger_folios_invoices(conn):
    assert set(ROLE_PERMISSIONS[ACCOUNTANT]) == {
        "folios.read",
        "ledger.read",
        "ledger.write",
        "invoices.read",
        "invoices.write",
        "finance.night_audit",
        "finance.tax_reports",
        "finance.refunds",
    }
    assert "folios.write" not in ROLE_PERMISSIONS[ACCOUNTANT]
    assert "reservations.check_in" not in ROLE_PERMISSIONS[ACCOUNTANT]
    assert "reservations.check_out" not in ROLE_PERMISSIONS[ACCOUNTANT]


def test_general_manager_staff_and_reports_without_hotel_manage(conn):
    from rbac import GENERAL_MANAGER, assignable_roles_for_actor

    grants = set(ROLE_PERMISSIONS[GENERAL_MANAGER])
    assert "hotel.staff.manage" in grants
    assert "reports.generate" in grants
    assert "hotel.manage" not in grants
    assert "finance.night_audit" not in grants
    assert "finance.refunds" not in grants
    assert set(assignable_roles_for_actor(GENERAL_MANAGER)) == {
        "HOTEL_MANAGER",
        "FRONT_DESK",
        "HOUSEKEEPING",
        "ACCOUNTANT",
    }


def test_manager_can_assign_branch_staff_only(conn):
    from rbac import assignable_roles_for_actor

    assert set(assignable_roles_for_actor(HOTEL_MANAGER)) == {
        "FRONT_DESK",
        "HOUSEKEEPING",
        "ACCOUNTANT",
    }
    assert "hotel.staff.manage" in ROLE_PERMISSIONS[HOTEL_MANAGER]
    assert "reports.generate" in ROLE_PERMISSIONS[HOTEL_MANAGER]


def test_hotel_accountant_mirrors_branch_accountant_plus_reports(conn):
    from rbac import HOTEL_ACCOUNTANT

    assert set(ROLE_PERMISSIONS[HOTEL_ACCOUNTANT]) == set(ROLE_PERMISSIONS[ACCOUNTANT]) | {
        "reports.generate",
    }


def test_hotel_admin_has_hotel_manage(conn):
    from rbac import HOTEL_ADMIN
    assert "hotel.manage" in ROLE_PERMISSIONS[HOTEL_ADMIN]
    hotel = str(uuid.uuid4())
    admin = _user(conn, "admin@a.test", HOTEL_ADMIN, tenant_id=hotel)
    assert has_permission(conn, admin, "hotel.manage", tenant_id=hotel) is True
    desk = _user(conn, "desk3@a.test", FRONT_DESK, tenant_id=hotel)
    assert has_permission(conn, desk, "hotel.manage", tenant_id=hotel) is False


def test_named_permission_tenant_scoped(conn):
    hotel_a = str(uuid.uuid4())
    hotel_b = str(uuid.uuid4())
    desk = _user(conn, "desk@a.test", FRONT_DESK, tenant_id=hotel_a)
    assert has_permission(conn, desk, "bookings.write", tenant_id=hotel_a) is True
    assert has_permission(conn, desk, "bookings.write", tenant_id=hotel_b) is False
    assert has_permission(conn, desk, "rooms.update_status", tenant_id=hotel_a) is False
    assert has_permission(conn, desk, "platform.admin", tenant_id=hotel_a) is False


def test_front_desk_cannot_toggle_status_or_read_ledger(conn):
    desk = _user(conn, "desk@a.test", FRONT_DESK, tenant_id=str(uuid.uuid4()))
    assert check_permission(conn, desk, "hospitalityclaw", "update-room-status") is False
    assert check_permission(conn, desk, "hospitalityclaw", "list-ledger-entries") is False
    assert check_permission(conn, desk, "hospitalityclaw", "check-in") is True
    assert check_permission(conn, desk, "hospitalityclaw", "settle-folio") is True
    assert check_permission(conn, desk, "hospitalityclaw", "list-folios") is True


def test_named_permissions_for_user(conn):
    from rbac import named_permissions_for_user
    hotel = str(uuid.uuid4())
    desk = _user(conn, "desk2@a.test", FRONT_DESK, tenant_id=hotel)
    perms = named_permissions_for_user(conn, desk)
    assert "bookings.write" in perms
    assert "rooms.update_status" not in perms
    assert "ledger.read" not in perms


def test_housekeeping_skill_actions(conn):
    hk = _user(conn, "hk@a.test", HOUSEKEEPING, tenant_id=str(uuid.uuid4()))
    assert check_permission(conn, hk, "hospitalityclaw", "update-room-status") is True
    assert check_permission(conn, hk, "hospitalityclaw", "list-rooms") is True
    assert check_permission(conn, hk, "hospitalityclaw", "add-reservation") is False
    assert check_permission(conn, hk, "hospitalityclaw", "check-in") is False
    assert check_permission(conn, hk, "hospitalityclaw", "list-folios") is False
    assert check_permission(conn, hk, "hospitalityclaw", "settle-folio") is False
    assert check_permission(conn, hk, "hospitalityclaw", "list-rate-plans") is False
    assert check_permission(conn, hk, "hospitalityclaw", "add-rate-plan") is False
    assert check_permission(conn, hk, "hospitalityclaw", "list-tax-rules") is False
    assert check_permission(conn, hk, "hospitalityclaw", "add-room") is False


def test_accountant_skill_actions(conn):
    acc = _user(conn, "acc@a.test", ACCOUNTANT, tenant_id=str(uuid.uuid4()))
    assert check_permission(conn, acc, "hospitalityclaw", "list-folios") is True
    assert check_permission(conn, acc, "erpclaw", "list-gl-entries") is True
    assert check_permission(conn, acc, "erpclaw", "list-sales-invoices") is True
    assert check_permission(conn, acc, "erpclaw", "add-journal-entry") is True
    assert check_permission(conn, acc, "erpclaw", "validate-period-close") is True
    assert check_permission(conn, acc, "hospitalityclaw", "check-in") is False
    assert check_permission(conn, acc, "hospitalityclaw", "check-out") is False
    assert check_permission(conn, acc, "hospitalityclaw", "add-reservation") is False
    # Refund override settle is allowed; routine desk ops are not
    assert check_permission(conn, acc, "hospitalityclaw", "settle-folio") is True


def test_front_desk_settles_folios(conn):
    desk = _user(conn, "desk-pay@a.test", FRONT_DESK, tenant_id=str(uuid.uuid4()))
    assert check_permission(conn, desk, "hospitalityclaw", "settle-folio") is True
    assert check_permission(conn, desk, "hospitalityclaw", "add-folio-charge") is True
    assert check_permission(conn, desk, "hospitalityclaw", "check-out") is True
    assert check_permission(conn, desk, "erpclaw", "add-journal-entry") is False
    assert check_permission(conn, desk, "erpclaw", "validate-period-close") is False


def test_super_admin_bypasses(conn):
    admin = _user(conn, "root@pms.test", SUPER_ADMIN, tenant_id=None)
    assert check_permission(conn, admin, "hospitalityclaw", "anything") is True
    assert has_permission(conn, admin, "platform.admin", tenant_id=None, is_superadmin=True)


def test_require_permission_decorator(conn, monkeypatch):
    hotel = str(uuid.uuid4())
    desk = _user(conn, "desk@a.test", FRONT_DESK, tenant_id=hotel)
    hk = _user(conn, "hk@a.test", HOUSEKEEPING, tenant_id=hotel)

    app = FastAPI()

    @app.get("/book")
    @require_permission("bookings.write")
    async def book(request: Request):
        return {"ok": True}

    @app.middleware("http")
    async def attach(request: Request, call_next):
        uid = request.headers.get("x-user")
        role = request.headers.get("x-role")
        ctx = TenantContext(
            user_id=uid,
            email="x@y.z",
            tenant_id=hotel,
            is_superadmin=False,
            roles=(ROLE_DISPLAY[role],),
        )
        request.state.tenant = ctx
        token = set_tenant_context(ctx)
        try:
            return await call_next(request)
        finally:
            reset_tenant_context(token)

    client = TestClient(app)
    ok = client.get("/book", headers={"x-user": desk, "x-role": FRONT_DESK})
    assert ok.status_code == 200
    denied = client.get("/book", headers={"x-user": hk, "x-role": HOUSEKEEPING})
    assert denied.status_code == 403
    assert "bookings.write" in denied.json()["detail"]
