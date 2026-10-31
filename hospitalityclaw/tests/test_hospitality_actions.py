"""HospitalityClaw action + tenant isolation tests."""
import argparse
from decimal import Decimal
import os
import sqlite3
import sys
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
LIB = os.path.abspath(os.path.join(ROOT, "..", "erpclaw-engine", "scripts", "erpclaw-setup", "lib"))
sys.path.insert(0, LIB)

from init_db import init_db
import db_query as hq


def _ns(**kwargs):
    defaults = dict(
        company_id=None, tenant_id=None, id=None, hotel_id=None, hotel_name=None,
        name=None, code=None, base_rate=None, max_occupancy=None, description=None,
        room_type_id=None, room_id=None, room_number=None, floor=None, status=None,
        guest_id=None, first_name=None, last_name=None, email=None, phone=None,
        nationality=None, id_number=None, notes=None, reservation_id=None,
        check_in=None, check_out=None, adults=None, children=None, rate=None,
        folio_id=None, amount=None, charge_type=None, posting_date=None,
        payment_method=None, reference=None, timezone=None, currency=None,
        country=None, address=None, limit=None, offset=None, search=None,
        from_date=None, to_date=None,
        start_date=None, end_date=None, nightly_rate=None, priority=None,
        is_active=None, is_default=None, applies_to=None, rate_percent=None,
        tax_template_id=None,
        available_from=None, cleaning_date=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class _Conn:
    """sqlite3.Connection wrapper so tests can hang tenant ids on it (py3.12)."""

    def __init__(self, conn, tenant_a, tenant_b):
        self._conn = conn
        self.tenant_a = tenant_a
        self.tenant_b = tenant_b

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def conn(monkeypatch):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute(
        "CREATE TABLE company (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "abbr TEXT, default_currency TEXT, country TEXT)"
    )
    c.execute(
        "CREATE TABLE audit_log (id TEXT PRIMARY KEY, timestamp TEXT, user_id TEXT, "
        "skill TEXT, action TEXT, entity_type TEXT, entity_id TEXT, "
        "old_values TEXT, new_values TEXT, description TEXT)"
    )
    t1 = str(uuid.uuid4())
    t2 = str(uuid.uuid4())
    c.execute("INSERT INTO company (id, name, abbr) VALUES (?, 'Hotel A', 'HA')", (t1,))
    c.execute("INSERT INTO company (id, name, abbr) VALUES (?, 'Hotel B', 'HB')", (t2,))
    init_db(c)
    monkeypatch.setenv("ERPCLAW_TENANT_STRICT", "0")
    monkeypatch.delenv("ERPCLAW_TENANT_ID", raising=False)
    return _Conn(c, t1, t2)


def _ok(monkeypatch):
    captured = {}

    def fake_ok(data):
        captured.update(data)
        captured["status"] = "ok"

    monkeypatch.setattr(hq, "ok", fake_ok)
    return captured


def test_hospitality_initialize_schema(conn, monkeypatch):
    out = _ok(monkeypatch)
    hq.hospitality_initialize_schema(conn, _ns())
    assert out["status"] == "ok"


def test_hospitality_seed_defaults(conn, monkeypatch):
    out = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="Alpha Inn"))
    assert out["hotel_id"]
    types = conn.execute(
        "SELECT code FROM hospitalityclaw_room_type WHERE tenant_id = ?",
        (conn.tenant_a,),
    ).fetchall()
    assert {r["code"] for r in types} == {"STD", "DLX", "STE"}


def test_hospitality_seed_defaults_uses_registration_currency(conn, monkeypatch):
    conn.execute(
        "CREATE TABLE currency (code TEXT PRIMARY KEY, name TEXT, symbol TEXT, "
        "decimal_places INTEGER DEFAULT 2, enabled INTEGER DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO currency (code, name, symbol, enabled) VALUES ('USD', 'US Dollar', '$', 1)"
    )
    out = _ok(monkeypatch)
    hq.hospitality_seed_defaults(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_name="Karachi Inn",
            currency="pkr",
            country="Pakistan",
            timezone="Asia/Karachi",
        ),
    )
    hotel = conn.execute(
        "SELECT currency, country, timezone FROM hospitalityclaw_hotel WHERE id = ?",
        (out["hotel_id"],),
    ).fetchone()
    assert hotel["currency"] == "PKR"
    assert hotel["country"] == "Pakistan"
    pkr = conn.execute("SELECT enabled FROM currency WHERE code = 'PKR'").fetchone()
    assert pkr["enabled"] == 1

    again = _ok(monkeypatch)
    hq.hospitality_seed_defaults(
        conn,
        _ns(tenant_id=conn.tenant_a, hotel_name="Karachi Inn", currency="AED"),
    )
    hotel = conn.execute(
        "SELECT currency FROM hospitalityclaw_hotel WHERE id = ?",
        (again["hotel_id"],),
    ).fetchone()
    assert hotel["currency"] == "AED"


def test_tenant_isolation_rooms_and_guests(conn, monkeypatch):
    _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_b, hotel_name="B"))
    a_rooms = conn.execute(
        "SELECT id FROM hospitalityclaw_hotel WHERE tenant_id = ?", (conn.tenant_a,)
    ).fetchall()
    b_rooms = conn.execute(
        "SELECT id FROM hospitalityclaw_hotel WHERE tenant_id = ?", (conn.tenant_b,)
    ).fetchall()
    assert a_rooms[0]["id"] != b_rooms[0]["id"]
    monkeypatch.setenv("ERPCLAW_TENANT_ID", conn.tenant_a)
    out = _ok(monkeypatch)
    hq.hospitality_list_hotels(conn, _ns())
    assert len(out["hotels"]) == 1
    assert out["hotels"][0]["name"] == "A"


def test_hospitality_status(conn, monkeypatch):
    _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    out = _ok(monkeypatch)
    hq.hospitality_status(conn, _ns(tenant_id=conn.tenant_a))
    assert out["hotels"] == 1
    assert "revenue" in out
    assert out["revenue"] == "0" or Decimal(str(out["revenue"])) == 0


def test_hospitality_status_date_range_filters_charges(conn, monkeypatch):
    from datetime import date, timedelta

    _, _, room, res = _book(conn, monkeypatch)
    cin = _ok(monkeypatch)
    hq.hospitality_check_in(
        conn, _ns(tenant_id=conn.tenant_a, id=res["reservation_id"], room_id=room["room_id"])
    )
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    far_past = (date.today() - timedelta(days=40)).isoformat()
    past_end = (date.today() - timedelta(days=30)).isoformat()

    _ok(monkeypatch)
    hq.hospitality_add_folio_charge(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            folio_id=cin["folio_id"],
            description="Today charge",
            amount="50",
            charge_type="other",
            posting_date=today,
        ),
    )

    scoped = _ok(monkeypatch)
    hq.hospitality_status(
        conn, _ns(tenant_id=conn.tenant_a, from_date=today, to_date=tomorrow)
    )
    assert Decimal(str(scoped["charges_total"])) >= Decimal("50")
    assert scoped["from_date"] == today

    empty = _ok(monkeypatch)
    hq.hospitality_status(
        conn, _ns(tenant_id=conn.tenant_a, from_date=far_past, to_date=past_end)
    )
    assert Decimal(str(empty["charges_total"])) == Decimal("0")


def test_list_revenue_includes_payments(conn, monkeypatch):
    _, _, room, res = _book(conn, monkeypatch)
    cin = _ok(monkeypatch)
    hq.hospitality_check_in(
        conn, _ns(tenant_id=conn.tenant_a, id=res["reservation_id"], room_id=room["room_id"])
    )
    _ok(monkeypatch)
    hq.hospitality_post_room_tariff(
        conn, _ns(tenant_id=conn.tenant_a, folio_id=cin["folio_id"])
    )
    balance = conn.execute(
        "SELECT balance FROM hospitalityclaw_folio WHERE id = ?", (cin["folio_id"],)
    ).fetchone()["balance"]
    assert Decimal(str(balance)) > 0
    _ok(monkeypatch)
    hq.hospitality_settle_folio(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            folio_id=cin["folio_id"],
            payment_method="card",
            reference="REV-1",
        ),
    )
    out = _ok(monkeypatch)
    hq.hospitality_list_revenue(conn, _ns(tenant_id=conn.tenant_a, limit=50))
    assert Decimal(str(out["revenue_total"])) == Decimal(str(balance))
    row = next(b for b in out["bookings"] if b["reservation_id"] == res["reservation_id"])
    assert Decimal(str(row["payments_total"])) == Decimal(str(balance))
    assert row["payments"]
    assert row["payments"][0]["payment_method"] == "card"
    status = _ok(monkeypatch)
    hq.hospitality_status(conn, _ns(tenant_id=conn.tenant_a))
    assert Decimal(str(status["revenue"])) == Decimal(str(balance))


def test_hospitality_list_hotels(conn, monkeypatch):
    _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    out = _ok(monkeypatch)
    hq.hospitality_list_hotels(conn, _ns(tenant_id=conn.tenant_a))
    assert out["hotels"]


def test_hospitality_get_hotel(conn, monkeypatch):
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    out = _ok(monkeypatch)
    hq.hospitality_get_hotel(conn, _ns(tenant_id=conn.tenant_a, id=seed["hotel_id"]))
    assert out["hotel"]["name"] == "A"


def test_hospitality_update_hotel(conn, monkeypatch):
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    out = _ok(monkeypatch)
    hq.hospitality_update_hotel(conn, _ns(tenant_id=conn.tenant_a, id=seed["hotel_id"], name="Renamed"))
    assert "name" in out["updated_fields"]


def test_hospitality_add_room_type(conn, monkeypatch):
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    out = _ok(monkeypatch)
    hq.hospitality_add_room_type(
        conn, _ns(tenant_id=conn.tenant_a, hotel_id=seed["hotel_id"], name="Penthouse", code="PTH", base_rate="500")
    )
    assert out["room_type_id"]


def test_hospitality_list_room_types(conn, monkeypatch):
    _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    out = _ok(monkeypatch)
    hq.hospitality_list_room_types(conn, _ns(tenant_id=conn.tenant_a))
    assert len(out["room_types"]) >= 3


def test_hospitality_add_room(conn, monkeypatch):
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    out = _ok(monkeypatch)
    hq.hospitality_add_room(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            room_type_id=seed["room_type_ids"][0],
            room_number="101",
        ),
    )
    assert out["room_id"]


def test_hospitality_list_rooms(conn, monkeypatch):
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    _ok(monkeypatch)
    hq.hospitality_add_room(
        conn,
        _ns(tenant_id=conn.tenant_a, hotel_id=seed["hotel_id"],
            room_type_id=seed["room_type_ids"][0], room_number="101"),
    )
    out = _ok(monkeypatch)
    hq.hospitality_list_rooms(conn, _ns(tenant_id=conn.tenant_a))
    assert out["rooms"]


def test_hospitality_update_room_status(conn, monkeypatch):
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    created = _ok(monkeypatch)
    hq.hospitality_add_room(
        conn,
        _ns(tenant_id=conn.tenant_a, hotel_id=seed["hotel_id"],
            room_type_id=seed["room_type_ids"][0], room_number="101"),
    )
    out = _ok(monkeypatch)
    hq.hospitality_update_room_status(
        conn, _ns(tenant_id=conn.tenant_a, id=created["room_id"], status="dirty")
    )
    assert out["room_status"] == "dirty"
    assert out.get("available_from")
    out = _ok(monkeypatch)
    hq.hospitality_update_room_status(
        conn, _ns(tenant_id=conn.tenant_a, id=created["room_id"], status="clean")
    )
    assert out["room_status"] == "vacant"
    assert out.get("available_from") is None
    out = _ok(monkeypatch)
    hq.hospitality_update_room_status(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            id=created["room_id"],
            status="maintenance",
        ),
    )
    assert out["room_status"] == "out_of_order"
    assert out["available_from"] is None


def test_hospitality_add_guest(conn, monkeypatch):
    out = _ok(monkeypatch)
    hq.hospitality_add_guest(conn, _ns(tenant_id=conn.tenant_a, first_name="Ada", last_name="Lovelace"))
    assert out["guest_id"]


def test_hospitality_list_guests(conn, monkeypatch):
    _ok(monkeypatch)
    hq.hospitality_add_guest(conn, _ns(tenant_id=conn.tenant_a, first_name="Ada", last_name="Lovelace"))
    out = _ok(monkeypatch)
    hq.hospitality_list_guests(conn, _ns(tenant_id=conn.tenant_a))
    assert len(out["guests"]) == 1


def test_hospitality_get_guest(conn, monkeypatch):
    created = _ok(monkeypatch)
    hq.hospitality_add_guest(conn, _ns(tenant_id=conn.tenant_a, first_name="Ada", last_name="Lovelace"))
    out = _ok(monkeypatch)
    hq.hospitality_get_guest(conn, _ns(tenant_id=conn.tenant_a, id=created["guest_id"]))
    assert out["guest"]["first_name"] == "Ada"


def _book(conn, monkeypatch):
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    guest = _ok(monkeypatch)
    hq.hospitality_add_guest(conn, _ns(tenant_id=conn.tenant_a, first_name="Ada", last_name="Lovelace"))
    room = _ok(monkeypatch)
    hq.hospitality_add_room(
        conn,
        _ns(tenant_id=conn.tenant_a, hotel_id=seed["hotel_id"],
            room_type_id=seed["room_type_ids"][0], room_number="101"),
    )
    res = _ok(monkeypatch)
    hq.hospitality_add_reservation(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            guest_id=guest["guest_id"],
            room_type_id=seed["room_type_ids"][0],
            room_id=room["room_id"],
            check_in="2026-09-01",
            check_out="2026-09-03",
        ),
    )
    return seed, guest, room, res


def test_hospitality_add_reservation(conn, monkeypatch):
    _, _, _, res = _book(conn, monkeypatch)
    assert res["reservation_id"]
    assert res["folio_id"]


def test_hospitality_list_reservations(conn, monkeypatch):
    _book(conn, monkeypatch)
    out = _ok(monkeypatch)
    hq.hospitality_list_reservations(conn, _ns(tenant_id=conn.tenant_a))
    assert out["reservations"]


def test_hospitality_list_reservations_paginates(conn, monkeypatch):
    _book(conn, monkeypatch)
    out = _ok(monkeypatch)
    hq.hospitality_list_reservations(
        conn, _ns(tenant_id=conn.tenant_a, limit=20, offset=0)
    )
    assert out["reservations"]
    assert out["total_count"] == 1
    assert out["has_more"] is False

    out = _ok(monkeypatch)
    hq.hospitality_list_reservations(
        conn, _ns(tenant_id=conn.tenant_a, limit=20, offset=1)
    )
    assert out["reservations"] == []
    assert out["total_count"] == 1


def test_hospitality_get_reservation(conn, monkeypatch):
    _, _, _, res = _book(conn, monkeypatch)
    out = _ok(monkeypatch)
    hq.hospitality_get_reservation(conn, _ns(tenant_id=conn.tenant_a, id=res["reservation_id"]))
    assert out["reservation"]["status"] == "reserved"
    assert out["reservation"]["first_name"] == "Ada"
    assert out["reservation"]["folio_id"]


def test_hospitality_assign_room(conn, monkeypatch):
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    guest = _ok(monkeypatch)
    hq.hospitality_add_guest(conn, _ns(tenant_id=conn.tenant_a, first_name="Ada", last_name="Lovelace"))
    room_b = _ok(monkeypatch)
    hq.hospitality_add_room(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            room_type_id=seed["room_type_ids"][0],
            room_number="102",
        ),
    )
    booking = _ok(monkeypatch)
    hq.hospitality_add_reservation(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            guest_id=guest["guest_id"],
            room_type_id=seed["room_type_ids"][0],
            check_in="2026-09-01",
            check_out="2026-09-03",
        ),
    )
    out = _ok(monkeypatch)
    hq.hospitality_assign_room(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            id=booking["reservation_id"],
            room_id=room_b["room_id"],
        ),
    )
    assert out["room_id"] == room_b["room_id"]
    row = conn.execute(
        "SELECT room_id FROM hospitalityclaw_reservation WHERE id = ?",
        (booking["reservation_id"],),
    ).fetchone()
    assert row["room_id"] == room_b["room_id"]

    # Wrong room type is rejected
    wrong = _ok(monkeypatch)
    hq.hospitality_add_room(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            room_type_id=seed["room_type_ids"][1],
            room_number="201",
        ),
    )
    with pytest.raises(SystemExit):
        hq.hospitality_assign_room(
            conn,
            _ns(
                tenant_id=conn.tenant_a,
                id=booking["reservation_id"],
                room_id=wrong["room_id"],
            ),
        )


def test_assign_room_same_day_requires_clean_and_honors_calendar(conn, monkeypatch):
    from datetime import date, timedelta

    today = date.today()
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    guest = _ok(monkeypatch)
    hq.hospitality_add_guest(
        conn, _ns(tenant_id=conn.tenant_a, first_name="Ada", last_name="Lovelace")
    )
    clean = _ok(monkeypatch)
    hq.hospitality_add_room(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            room_type_id=seed["room_type_ids"][0],
            room_number="101",
            status="vacant",
        ),
    )
    dirty = _ok(monkeypatch)
    hq.hospitality_add_room(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            room_type_id=seed["room_type_ids"][0],
            room_number="102",
            status="dirty",
        ),
    )

    same_day = _ok(monkeypatch)
    hq.hospitality_add_reservation(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            guest_id=guest["guest_id"],
            room_type_id=seed["room_type_ids"][0],
            check_in=today.isoformat(),
            check_out=(today + timedelta(days=2)).isoformat(),
        ),
    )
    with pytest.raises(SystemExit):
        hq.hospitality_assign_room(
            conn,
            _ns(
                tenant_id=conn.tenant_a,
                id=same_day["reservation_id"],
                room_id=dirty["room_id"],
            ),
        )

    # Dirty OK from day-after-tomorrow onward
    future = _ok(monkeypatch)
    hq.hospitality_add_reservation(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            guest_id=guest["guest_id"],
            room_type_id=seed["room_type_ids"][0],
            check_in=(today + timedelta(days=2)).isoformat(),
            check_out=(today + timedelta(days=4)).isoformat(),
        ),
    )
    out = _ok(monkeypatch)
    hq.hospitality_assign_room(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            id=future["reservation_id"],
            room_id=dirty["room_id"],
        ),
    )
    assert out["room_id"] == dirty["room_id"]

    # Calendar: clean room already taken for overlapping dates
    clash = _ok(monkeypatch)
    hq.hospitality_add_reservation(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            guest_id=guest["guest_id"],
            room_type_id=seed["room_type_ids"][0],
            room_id=clean["room_id"],
            check_in=(today + timedelta(days=10)).isoformat(),
            check_out=(today + timedelta(days=12)).isoformat(),
        ),
    )
    other = _ok(monkeypatch)
    hq.hospitality_add_reservation(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            guest_id=guest["guest_id"],
            room_type_id=seed["room_type_ids"][0],
            check_in=(today + timedelta(days=11)).isoformat(),
            check_out=(today + timedelta(days=13)).isoformat(),
        ),
    )
    with pytest.raises(SystemExit):
        hq.hospitality_assign_room(
            conn,
            _ns(
                tenant_id=conn.tenant_a,
                id=other["reservation_id"],
                room_id=clean["room_id"],
            ),
        )


def test_add_reservation_blocked_when_only_dirty_room_for_dates(conn, monkeypatch):
    from datetime import date, timedelta

    today = date.today()
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    guest = _ok(monkeypatch)
    hq.hospitality_add_guest(
        conn, _ns(tenant_id=conn.tenant_a, first_name="Ada", last_name="Lovelace")
    )
    room = _ok(monkeypatch)
    hq.hospitality_add_room(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            room_type_id=seed["room_type_ids"][0],
            room_number="101",
        ),
    )
    _ok(monkeypatch)
    hq.hospitality_update_room_status(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            id=room["room_id"],
            status="dirty",
            cleaning_date=(today + timedelta(days=3)).isoformat(),
        ),
    )
    # Same-day / before available_from must fail even with assign-later
    with pytest.raises(SystemExit):
        hq.hospitality_add_reservation(
            conn,
            _ns(
                tenant_id=conn.tenant_a,
                hotel_id=seed["hotel_id"],
                guest_id=guest["guest_id"],
                room_type_id=seed["room_type_ids"][0],
                check_in=today.isoformat(),
                check_out=(today + timedelta(days=1)).isoformat(),
            ),
        )
    # After schedule: bookable
    available = (today + timedelta(days=4)).isoformat()
    out = _ok(monkeypatch)
    hq.hospitality_add_reservation(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            guest_id=guest["guest_id"],
            room_type_id=seed["room_type_ids"][0],
            check_in=available,
            check_out=(today + timedelta(days=5)).isoformat(),
        ),
    )
    assert out["reservation_id"]


def test_cancel_reservation_before_check_in(conn, monkeypatch):
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    guest = _ok(monkeypatch)
    hq.hospitality_add_guest(
        conn, _ns(tenant_id=conn.tenant_a, first_name="Ada", last_name="Lovelace")
    )
    room = _ok(monkeypatch)
    hq.hospitality_add_room(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            room_type_id=seed["room_type_ids"][0],
            room_number="101",
        ),
    )
    booking = _ok(monkeypatch)
    hq.hospitality_add_reservation(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            guest_id=guest["guest_id"],
            room_type_id=seed["room_type_ids"][0],
            room_id=room["room_id"],
            check_in="2026-11-01",
            check_out="2026-11-03",
        ),
    )
    out = _ok(monkeypatch)
    hq.hospitality_cancel_reservation(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            id=booking["reservation_id"],
            notes="Guest cancelled",
        ),
    )
    assert out["reservation_status"] == "cancelled"
    assert out["folio_status"] == "cancelled"
    assert out["room_released"] is True
    row = conn.execute(
        "SELECT status, room_id, notes FROM hospitalityclaw_reservation WHERE id = ?",
        (booking["reservation_id"],),
    ).fetchone()
    assert row["status"] == "cancelled"
    assert row["room_id"] is None
    assert "Guest cancelled" in (row["notes"] or "")
    folio = conn.execute(
        "SELECT status FROM hospitalityclaw_folio WHERE reservation_id = ?",
        (booking["reservation_id"],),
    ).fetchone()
    assert folio["status"] == "cancelled"

    status = _ok(monkeypatch)
    hq.hospitality_status(conn, _ns(tenant_id=conn.tenant_a))
    assert status["reservations"] == 0
    assert status["folios"] == 0

    with pytest.raises(SystemExit):
        hq.hospitality_cancel_reservation(
            conn, _ns(tenant_id=conn.tenant_a, id=booking["reservation_id"])
        )


def test_hospitality_check_in(conn, monkeypatch):
    _, _, room, res = _book(conn, monkeypatch)
    out = _ok(monkeypatch)
    hq.hospitality_check_in(
        conn, _ns(tenant_id=conn.tenant_a, id=res["reservation_id"], room_id=room["room_id"])
    )
    assert out["folio_id"]


def test_hospitality_add_folio_charge(conn, monkeypatch):
    _, _, room, res = _book(conn, monkeypatch)
    _ok(monkeypatch)
    hq.hospitality_check_in(
        conn, _ns(tenant_id=conn.tenant_a, id=res["reservation_id"], room_id=room["room_id"])
    )
    out = _ok(monkeypatch)
    hq.hospitality_add_folio_charge(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            folio_id=res["folio_id"],
            description="Minibar",
            amount="12.50",
            charge_type="fnb",
        ),
    )
    assert out["charge_id"]


def test_hospitality_list_folio_charges(conn, monkeypatch):
    _, _, room, res = _book(conn, monkeypatch)
    _ok(monkeypatch)
    hq.hospitality_check_in(
        conn, _ns(tenant_id=conn.tenant_a, id=res["reservation_id"], room_id=room["room_id"])
    )
    out = _ok(monkeypatch)
    hq.hospitality_list_folio_charges(conn, _ns(tenant_id=conn.tenant_a, folio_id=res["folio_id"]))
    assert out["charges"]


def test_hospitality_list_ledger_entries(conn, monkeypatch):
    _, _, room, res = _book(conn, monkeypatch)
    _ok(monkeypatch)
    hq.hospitality_check_in(
        conn, _ns(tenant_id=conn.tenant_a, id=res["reservation_id"], room_id=room["room_id"])
    )
    out = _ok(monkeypatch)
    hq.hospitality_list_ledger_entries(conn, _ns(tenant_id=conn.tenant_a, folio_id=res["folio_id"]))
    assert out["entries"]


def test_hospitality_list_transactions(conn, monkeypatch):
    _, _, room, res = _book(conn, monkeypatch)
    _ok(monkeypatch)
    hq.hospitality_check_in(
        conn, _ns(tenant_id=conn.tenant_a, id=res["reservation_id"], room_id=room["room_id"])
    )
    _ok(monkeypatch)
    hq.hospitality_add_folio_charge(
        conn,
        _ns(
            tenant_id=conn.tenant_a, folio_id=res["folio_id"],
            description="Card payment", amount="100", charge_type="payment",
            payment_method="card",
        ),
    )
    out = _ok(monkeypatch)
    hq.hospitality_list_transactions(conn, _ns(tenant_id=conn.tenant_a, folio_id=res["folio_id"]))
    assert out["transactions"]


def test_hospitality_check_out(conn, monkeypatch):
    _, _, room, res = _book(conn, monkeypatch)
    cin = _ok(monkeypatch)
    hq.hospitality_check_in(
        conn, _ns(tenant_id=conn.tenant_a, id=res["reservation_id"], room_id=room["room_id"])
    )
    # Settle folio
    bal = conn.execute(
        "SELECT balance FROM hospitalityclaw_folio WHERE id = ?", (cin["folio_id"],)
    ).fetchone()["balance"]
    _ok(monkeypatch)
    hq.hospitality_add_folio_charge(
        conn,
        _ns(
            tenant_id=conn.tenant_a, folio_id=cin["folio_id"],
            description="Settle", amount=bal, charge_type="payment",
        ),
    )
    out = _ok(monkeypatch)
    hq.hospitality_check_out(conn, _ns(tenant_id=conn.tenant_a, id=res["reservation_id"]))
    assert out["reservation_status"] == "checked_out"


def test_cross_tenant_get_is_denied(conn, monkeypatch):
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    monkeypatch.setenv("ERPCLAW_TENANT_ID", conn.tenant_b)
    with pytest.raises(SystemExit):
        hq.hospitality_get_hotel(conn, _ns(id=seed["hotel_id"]))


def test_hospitality_update_and_delete_room_type(conn, monkeypatch):
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    created = _ok(monkeypatch)
    hq.hospitality_add_room_type(
        conn, _ns(tenant_id=conn.tenant_a, hotel_id=seed["hotel_id"], name="Penthouse", code="PTH", base_rate="500")
    )
    out = _ok(monkeypatch)
    hq.hospitality_update_room_type(
        conn, _ns(tenant_id=conn.tenant_a, id=created["room_type_id"], name="Penthouse Suite", base_rate="650")
    )
    assert "name" in out["updated_fields"]
    out = _ok(monkeypatch)
    hq.hospitality_delete_room_type(conn, _ns(tenant_id=conn.tenant_a, id=created["room_type_id"]))
    assert out["deleted"] is True


def test_hospitality_update_and_delete_room(conn, monkeypatch):
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    created = _ok(monkeypatch)
    hq.hospitality_add_room(
        conn,
        _ns(tenant_id=conn.tenant_a, hotel_id=seed["hotel_id"],
            room_type_id=seed["room_type_ids"][0], room_number="101"),
    )
    out = _ok(monkeypatch)
    hq.hospitality_update_room(
        conn, _ns(tenant_id=conn.tenant_a, id=created["room_id"], floor="1")
    )
    assert "floor" in out["updated_fields"]
    out = _ok(monkeypatch)
    hq.hospitality_delete_room(conn, _ns(tenant_id=conn.tenant_a, id=created["room_id"]))
    assert out["deleted"] is True


def test_hospitality_settle_folio(conn, monkeypatch):
    _, _, room, res = _book(conn, monkeypatch)
    cin = _ok(monkeypatch)
    hq.hospitality_check_in(
        conn, _ns(tenant_id=conn.tenant_a, id=res["reservation_id"], room_id=room["room_id"])
    )
    out = _ok(monkeypatch)
    hq.hospitality_settle_folio(conn, _ns(tenant_id=conn.tenant_a, folio_id=cin["folio_id"]))
    assert out["settled"] is True
    assert "gl_posted" in out
    bal = conn.execute(
        "SELECT balance FROM hospitalityclaw_folio WHERE id = ?", (cin["folio_id"],)
    ).fetchone()["balance"]
    assert Decimal(str(bal)) == 0
    out = _ok(monkeypatch)
    hq.hospitality_check_out(conn, _ns(tenant_id=conn.tenant_a, id=res["reservation_id"]))
    assert out["reservation_status"] == "checked_out"


def test_rate_plan_and_tax_rule_crud(conn, monkeypatch):
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    plan = _ok(monkeypatch)
    hq.hospitality_add_rate_plan(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            room_type_id=seed["room_type_ids"][0],
            name="Summer Peak",
            code="SUM26",
            start_date="2026-06-01",
            end_date="2026-08-31",
            nightly_rate="250",
            priority="20",
        ),
    )
    assert plan["rate_plan_id"]
    listed = _ok(monkeypatch)
    hq.hospitality_list_rate_plans(conn, _ns(tenant_id=conn.tenant_a))
    assert any(p["code"] == "SUM26" for p in listed["rate_plans"])

    tax = _ok(monkeypatch)
    hq.hospitality_add_tax_rule(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            name="City Tax",
            rate_percent="5",
            applies_to="fnb",
        ),
    )
    assert tax["tax_rule_id"]
    taxes = _ok(monkeypatch)
    hq.hospitality_list_tax_rules(conn, _ns(tenant_id=conn.tenant_a))
    names = {t["name"] for t in taxes["tax_rules"]}
    assert "Occupancy Tax" in names
    assert "City Tax" in names


def test_room_tariff_applies_seasonal_rate_and_tax(conn, monkeypatch):
    seed, _guest, room, res = _book(conn, monkeypatch)
    _ok(monkeypatch)
    hq.hospitality_add_rate_plan(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            room_type_id=seed["room_type_ids"][0],
            name="Promo",
            code="PROMO",
            start_date="2020-01-01",
            end_date="2099-12-31",
            nightly_rate="175",
            priority="50",
        ),
    )
    cin = _ok(monkeypatch)
    hq.hospitality_check_in(
        conn, _ns(tenant_id=conn.tenant_a, id=res["reservation_id"], room_id=room["room_id"])
    )
    out = _ok(monkeypatch)
    hq.hospitality_post_room_tariff(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            folio_id=cin["folio_id"],
            posting_date="2026-07-15",
        ),
    )
    assert out["posted_count"] == 1
    charges = conn.execute(
        """SELECT charge_type, amount FROM hospitalityclaw_folio_charge
           WHERE folio_id = ? AND posting_date = '2026-07-15'
           ORDER BY charge_type""",
        (cin["folio_id"],),
    ).fetchall()
    room_lines = [c for c in charges if c["charge_type"] == "room"]
    tax_lines = [c for c in charges if c["charge_type"] == "tax"]
    assert room_lines and Decimal(str(room_lines[0]["amount"])) == Decimal("175")
    assert tax_lines


def test_hospitality_post_room_tariff_is_idempotent(conn, monkeypatch):
    _, _, room, res = _book(conn, monkeypatch)
    cin = _ok(monkeypatch)
    hq.hospitality_check_in(
        conn, _ns(tenant_id=conn.tenant_a, id=res["reservation_id"], room_id=room["room_id"])
    )
    # Check-in already posted today's room charge
    out = _ok(monkeypatch)
    hq.hospitality_post_room_tariff(
        conn, _ns(tenant_id=conn.tenant_a, folio_id=cin["folio_id"])
    )
    assert out["posted_count"] == 0
    assert any(s.get("already_posted") for s in out["skipped"])
    out = _ok(monkeypatch)
    hq.hospitality_post_room_tariff(
        conn, _ns(tenant_id=conn.tenant_a, folio_id=cin["folio_id"], posting_date="2099-01-01")
    )
    assert out["posted_count"] == 1
    entry = conn.execute(
        "SELECT account_code FROM hospitalityclaw_ledger_entry WHERE folio_id = ? AND account_code = 'ROOM_REV'",
        (cin["folio_id"],),
    ).fetchone()
    assert entry is not None


def test_list_reservations_includes_guest_and_folio(conn, monkeypatch):
    _book(conn, monkeypatch)
    out = _ok(monkeypatch)
    hq.hospitality_list_reservations(conn, _ns(tenant_id=conn.tenant_a))
    row = out["reservations"][0]
    assert row["first_name"] == "Ada"
    assert row["folio_id"]
    assert row["room_type_name"]


def test_dynamic_rate_windows_and_calendar(conn, monkeypatch):
    seed = _ok(monkeypatch)
    hq.hospitality_seed_defaults(conn, _ns(tenant_id=conn.tenant_a, hotel_name="A"))
    hotel_id = seed["hotel_id"]
    room_type_id = seed["room_type_ids"][0]
    base = conn.execute(
        "SELECT base_rate FROM hospitalityclaw_room_type WHERE id = ?",
        (room_type_id,),
    ).fetchone()["base_rate"]

    month = _ok(monkeypatch)
    hq.hospitality_set_rate_window(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=hotel_id,
            room_type_id=room_type_id,
            start_date="2026-10-01",
            end_date="2026-10-31",
            nightly_rate="100",
        ),
    )
    assert month["updated"] is False
    assert Decimal(month["nightly_rate"]) == Decimal("100")

    peak = _ok(monkeypatch)
    hq.hospitality_set_rate_window(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=hotel_id,
            room_type_id=room_type_id,
            start_date="2026-10-11",
            end_date="2026-10-23",
            nightly_rate="150",
        ),
    )
    assert Decimal(str(peak["priority"])) > Decimal(str(month["priority"]))

    cal = _ok(monkeypatch)
    hq.hospitality_rate_calendar(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=hotel_id,
            room_type_id=room_type_id,
            start_date="2026-10-01",
            end_date="2026-10-31",
        ),
    )
    by_date = {n["date"]: n for n in cal["nightly_rates"]}
    assert Decimal(by_date["2026-10-05"]["rate"]) == Decimal("100")
    assert Decimal(by_date["2026-10-15"]["rate"]) == Decimal("150")
    assert "2026-09-30" not in by_date
    # Outside October falls back to the room-type base rate
    outside = _ok(monkeypatch)
    hq.hospitality_rate_calendar(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=hotel_id,
            room_type_id=room_type_id,
            check_in="2026-11-01",
            check_out="2026-11-02",
        ),
    )
    assert Decimal(outside["nightly_rates"][0]["rate"]) == Decimal("100")
    assert outside["nightly_rates"][0]["source"] == "base"
    assert Decimal(str(base)) == Decimal("100")

    # Upsert same window updates in place
    again = _ok(monkeypatch)
    hq.hospitality_set_rate_window(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=hotel_id,
            room_type_id=room_type_id,
            start_date="2026-10-01",
            end_date="2026-10-31",
            nightly_rate="110",
        ),
    )
    assert again["updated"] is True
    assert again["rate_plan_id"] == month["rate_plan_id"]
    assert Decimal(again["nightly_rate"]) == Decimal("110")


def test_add_reservation_uses_dynamic_check_in_rate(conn, monkeypatch):
    seed, guest, _room, _ = _book(conn, monkeypatch)
    _ok(monkeypatch)
    hq.hospitality_set_rate_window(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            room_type_id=seed["room_type_ids"][0],
            start_date="2026-10-01",
            end_date="2026-10-31",
            nightly_rate="222",
        ),
    )
    res = _ok(monkeypatch)
    hq.hospitality_add_reservation(
        conn,
        _ns(
            tenant_id=conn.tenant_a,
            hotel_id=seed["hotel_id"],
            guest_id=guest["guest_id"],
            room_type_id=seed["room_type_ids"][0],
            check_in="2026-10-05",
            check_out="2026-10-07",
        ),
    )
    assert Decimal(res["rate"]) == Decimal("222")
