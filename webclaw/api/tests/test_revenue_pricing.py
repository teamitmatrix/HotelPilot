"""Dynamic pricing / yield management tests."""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pgutil import connect_erp

from services import revenue as rv


def _connect(_ignored=None):
    return connect_erp()


@pytest.fixture
def hotel(setup_dbs, monkeypatch):
    tid = str(uuid.uuid4())
    hotel_id = str(uuid.uuid4())
    rt_id = str(uuid.uuid4())
    room_ids = [str(uuid.uuid4()) for _ in range(5)]

    conn = connect_erp()
    rv.ensure_schema(conn)
    abbr = tid.replace("-", "")[:8]
    conn.execute(
        "INSERT INTO company (id, name, abbr) VALUES (?, ?, ?)",
        (tid, f"Co-{abbr}", abbr),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_hotel
           (id, tenant_id, name, currency, timezone) VALUES (?, ?, 'Harbor Inn', 'USD', 'UTC')""",
        (hotel_id, tid),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_room_type
           (id, tenant_id, hotel_id, name, code, base_rate, max_occupancy)
           VALUES (?, ?, ?, 'Deluxe', 'DLX', '100.00', 2)""",
        (rt_id, tid, hotel_id),
    )
    for i, rid in enumerate(room_ids, start=1):
        conn.execute(
            """INSERT INTO hospitalityclaw_room
               (id, tenant_id, hotel_id, room_type_id, room_number, status)
               VALUES (?, ?, ?, ?, ?, 'vacant')""",
            (rid, tid, hotel_id, rt_id, str(100 + i)),
        )
    conn.commit()
    conn.close()

    return {
        "erp": None,
        "tenant_id": tid,
        "hotel_id": hotel_id,
        "room_type_id": rt_id,
        "room_ids": room_ids,
        "today": date.today(),
    }


def _book(hotel, rooms_sold: int, check_in: str, check_out: str):
    conn = _connect(hotel["erp"])
    for i in range(rooms_sold):
        guest_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO hospitalityclaw_guest (id, tenant_id, first_name, last_name)
               VALUES (?, ?, 'Guest', ?)""",
            (guest_id, hotel["tenant_id"], str(i)),
        )
        conn.execute(
            """INSERT INTO hospitalityclaw_reservation
               (id, tenant_id, hotel_id, guest_id, room_id, room_type_id,
                check_in, check_out, adults, status, rate)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'reserved', '100.00')""",
            (
                str(uuid.uuid4()), hotel["tenant_id"], hotel["hotel_id"], guest_id,
                hotel["room_ids"][i], hotel["room_type_id"], check_in, check_out,
            ),
        )
    conn.commit()
    conn.close()


def test_high_occupancy_increases_rate_and_notifies_channel(hotel):
    tomorrow = (hotel["today"] + timedelta(days=1)).isoformat()
    day_after = (hotel["today"] + timedelta(days=2)).isoformat()
    # 4/5 = 80% occupancy tomorrow
    _book(hotel, 4, tomorrow, day_after)

    rule = rv.save_rule(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "name": "Busy night uplift",
        "rule_kind": "occupancy",
        "room_type_id": hotel["room_type_id"],
        "priority": 50,
        "occupancy_op": "gte",
        "occupancy_pct": "80",
        "adjustment_pct": "15",
    })
    assert "≥" in rule["summary"] or "increase by 15%" in rule["summary"]

    pushes = []

    def fake_push(tenant_id, **kwargs):
        pushes.append(kwargs)
        return {"status": "ok", "pushed": [{"ota": "booking_com"}]}

    with patch("services.channel_manager.push_outbound", fake_push):
        result = rv.evaluate_hotel(
            hotel["tenant_id"], hotel["hotel_id"], horizon_days=3, push_channels=True,
        )

    assert result["status"] == "ok"
    assert result["rates_written"] >= 1
    assert pushes and pushes[0]["reason"] == "yield-pricing"

    conn = _connect(hotel["erp"])
    plan = conn.execute(
        """SELECT nightly_rate, priority, code, is_active FROM hospitalityclaw_rate_plan
           WHERE tenant_id = ? AND start_date = ? AND code LIKE 'YLD-%'""",
        (hotel["tenant_id"], tomorrow),
    ).fetchone()
    assert plan is not None
    assert plan["nightly_rate"] == "115.00"
    assert plan["is_active"] == 1
    assert int(plan["priority"]) >= 900
    conn.close()


def test_low_occupancy_last_minute_discount(hotel):
    today = hotel["today"].isoformat()
    tomorrow = (hotel["today"] + timedelta(days=1)).isoformat()
    # 1/5 = 20% occupancy today
    _book(hotel, 1, today, tomorrow)

    rv.save_rule(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "name": "Last-minute fill",
        "rule_kind": "occupancy",
        "occupancy_op": "lt",
        "occupancy_pct": "30",
        "within_hours": 48,
        "adjustment_pct": "-10",
        "priority": 40,
    })

    with patch("services.channel_manager.push_outbound", lambda *a, **k: {"status": "ok"}):
        result = rv.evaluate_hotel(
            hotel["tenant_id"], hotel["hotel_id"], horizon_days=2, push_channels=False,
            now=datetime.now(timezone.utc),
        )

    assert result["rates_written"] >= 1
    conn = _connect(hotel["erp"])
    plan = conn.execute(
        """SELECT nightly_rate FROM hospitalityclaw_rate_plan
           WHERE tenant_id = ? AND start_date = ? AND code LIKE 'YLD-%' AND is_active = 1""",
        (hotel["tenant_id"], today),
    ).fetchone()
    assert plan["nightly_rate"] == "90.00"
    base = conn.execute(
        "SELECT base_rate FROM hospitalityclaw_room_type WHERE id = ?",
        (hotel["room_type_id"],),
    ).fetchone()
    assert base["base_rate"] == "90.00"
    conn.close()


def test_weekend_min_los_restriction_and_booking_block(hotel):
    # Find next Saturday
    day = hotel["today"]
    while day.weekday() != 5:
        day += timedelta(days=1)
    saturday = day.isoformat()
    sunday = (day + timedelta(days=1)).isoformat()

    rv.save_rule(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "name": "Weekend 2-night minimum",
        "rule_kind": "min_los",
        "room_type_id": hotel["room_type_id"],
        "min_nights": 2,
        "days_of_week": ["sat", "sun"],
        "priority": 10,
    })

    with patch("services.channel_manager.push_outbound", lambda *a, **k: {"status": "ok"}):
        # Force evaluation as if "today" is that Friday so Saturday is in horizon
        friday = day - timedelta(days=1)
        result = rv.evaluate_hotel(
            hotel["tenant_id"], hotel["hotel_id"], horizon_days=4, push_channels=False,
            now=datetime(friday.year, friday.month, friday.day, 12, 0, tzinfo=timezone.utc),
        )
    assert result["restrictions_written"] >= 1

    conn = _connect(hotel["erp"])
    rest = conn.execute(
        """SELECT min_nights FROM hospitalityclaw_stay_restriction
           WHERE tenant_id = ? AND stay_date = ? AND room_type_id = ?""",
        (hotel["tenant_id"], saturday, hotel["room_type_id"]),
    ).fetchone()
    assert rest["min_nights"] == 2

    required = rv.required_min_nights(
        conn, hotel["tenant_id"], hotel["hotel_id"], hotel["room_type_id"], saturday, sunday,
    )
    assert required == 2
    conn.close()


def test_toggle_and_priority_dashboard(hotel):
    rule = rv.save_rule(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "name": "Uplift",
        "rule_kind": "occupancy",
        "occupancy_op": "gte",
        "occupancy_pct": "70",
        "adjustment_pct": "10",
        "priority": 5,
    })
    toggled = rv.set_rule_active(hotel["tenant_id"], rule["id"], False)
    assert toggled["is_active"] is False
    dash = rv.dashboard(hotel["tenant_id"])
    assert any(r["id"] == rule["id"] and not r["is_active"] for r in dash["rules"])


def test_scheduler_skips_already_ran(hotel):
    rv.save_rule(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "name": "Uplift",
        "rule_kind": "occupancy",
        "occupancy_op": "gt",
        "occupancy_pct": "99",
        "adjustment_pct": "5",
    })
    with patch("services.channel_manager.push_outbound", lambda *a, **k: {"status": "ok"}):
        first = rv.evaluate_hotel(hotel["tenant_id"], hotel["hotel_id"], push_channels=False)
        assert first["status"] == "ok"
        assert rv.already_ran_today(hotel["tenant_id"], hotel["hotel_id"]) is True

    import asyncio
    with patch.object(rv, "evaluate_hotel") as mocked:
        summary = asyncio.run(rv.run_due_yield_jobs(force=False))
        assert mocked.call_count == 0
        assert summary["skipped"]
