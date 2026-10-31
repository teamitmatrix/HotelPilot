"""Availability grid, restrictions, and overbooking helpers."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pgutil import connect_erp

from services import inventory_controls as inv
from services import channel_manager as cm


@pytest.fixture
def hotel(setup_dbs):
    tid = str(uuid.uuid4())
    hotel_id = str(uuid.uuid4())
    rt_id = str(uuid.uuid4())
    room_id = str(uuid.uuid4())
    today = date.today()

    conn = connect_erp()
    cm.ensure_schema(conn)
    inv.ensure_schema(conn)
    abbr = tid.replace("-", "")[:8]
    conn.execute(
        "INSERT INTO company (id, name, abbr) VALUES (?, ?, ?)",
        (tid, f"Co-{abbr}", abbr),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_hotel
           (id, tenant_id, name, currency, timezone) VALUES (?, ?, 'Flash Inn', 'USD', 'UTC')""",
        (hotel_id, tid),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_room_type
           (id, tenant_id, hotel_id, name, code, base_rate, max_occupancy)
           VALUES (?, ?, ?, 'Deluxe', 'DLX', '100.00', 2)""",
        (rt_id, tid, hotel_id),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_room
           (id, tenant_id, hotel_id, room_type_id, room_number, status)
           VALUES (?, ?, ?, ?, '101', 'vacant')""",
        (room_id, tid, hotel_id, rt_id),
    )
    conn.commit()
    conn.close()
    return {
        "tenant_id": tid,
        "hotel_id": hotel_id,
        "room_type_id": rt_id,
        "room_id": room_id,
        "today": today,
    }


def test_availability_grid_and_stop_sell(hotel):
    start = hotel["today"].isoformat()
    end = (hotel["today"] + timedelta(days=2)).isoformat()
    grid = inv.availability_grid(
        hotel["tenant_id"], hotel["hotel_id"], start_date=start, end_date=end,
    )
    assert grid["room_types"]
    cell = next(c for c in grid["cells"] if c["date"] == start)
    assert cell["sellable"] >= 1

    inv.upsert_controls(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "room_type_id": hotel["room_type_id"],
        "start_date": start,
        "end_date": start,
        "stop_sell": True,
        "min_nights": 1,
    })
    grid2 = inv.availability_grid(
        hotel["tenant_id"], hotel["hotel_id"], start_date=start, end_date=end,
    )
    cell2 = next(
        c for c in grid2["cells"]
        if c["date"] == start and c["room_type_id"] == hotel["room_type_id"]
    )
    assert cell2["stop_sell"] is True
    assert cell2["sellable"] == 0

    conn = connect_erp()
    with pytest.raises(ValueError, match="Stop-sell"):
        inv.assert_stay_allowed(
            conn,
            hotel["tenant_id"],
            hotel["hotel_id"],
            hotel["room_type_id"],
            start,
            (hotel["today"] + timedelta(days=1)).isoformat(),
        )
    conn.close()


def test_cta_and_max_los(hotel):
    start = (hotel["today"] + timedelta(days=5)).isoformat()
    end = (hotel["today"] + timedelta(days=8)).isoformat()
    inv.upsert_controls(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "room_type_id": hotel["room_type_id"],
        "start_date": start,
        "end_date": start,
        "closed_to_arrival": True,
        "max_nights": 2,
        "min_nights": 1,
    })
    conn = connect_erp()
    with pytest.raises(ValueError, match="Closed to arrival"):
        inv.assert_stay_allowed(
            conn,
            hotel["tenant_id"],
            hotel["hotel_id"],
            hotel["room_type_id"],
            start,
            (hotel["today"] + timedelta(days=6)).isoformat(),
        )
    conn.close()
    inv.clear_controls(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "room_type_id": hotel["room_type_id"],
        "start_date": start,
        "end_date": start,
    })
    inv.upsert_controls(hotel["tenant_id"], {
        "hotel_id": hotel["hotel_id"],
        "room_type_id": hotel["room_type_id"],
        "start_date": start,
        "end_date": end,
        "max_nights": 2,
        "min_nights": 1,
    })
    conn = connect_erp()
    with pytest.raises(ValueError, match="Maximum stay"):
        inv.assert_stay_allowed(
            conn,
            hotel["tenant_id"],
            hotel["hotel_id"],
            hotel["room_type_id"],
            start,
            end,
        )
    conn.close()
