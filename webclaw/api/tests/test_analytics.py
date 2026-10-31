"""Hospitality BI analytics — RevPAR, ADR, occupancy, exports."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pgutil import connect_erp

from services import analytics as ax


@pytest.fixture
def hotel(setup_dbs, monkeypatch):
    tid = str(uuid.uuid4())
    hotel_id = str(uuid.uuid4())
    rt_id = str(uuid.uuid4())
    rooms = [str(uuid.uuid4()) for _ in range(10)]
    today = date(2026, 10, 15)

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)
    from hospitalityclaw.init_db import init_db
    from services.channel_manager import ensure_schema as ensure_channels

    conn = connect_erp()
    init_db(conn)
    ensure_channels(conn)
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
           (id, tenant_id, hotel_id, name, code, base_rate)
           VALUES (?, ?, ?, 'Deluxe', 'DLX', '100.00')""",
        (rt_id, tid, hotel_id),
    )
    for i, rid in enumerate(rooms, start=1):
        conn.execute(
            """INSERT INTO hospitalityclaw_room
               (id, tenant_id, hotel_id, room_type_id, room_number, status)
               VALUES (?, ?, ?, ?, ?, 'vacant')""",
            (rid, tid, hotel_id, rt_id, str(100 + i)),
        )

    # 5 rooms occupied on Oct 15 → 50% occupancy for that night
    for i in range(5):
        guest = str(uuid.uuid4())
        res = str(uuid.uuid4())
        folio = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO hospitalityclaw_guest (id, tenant_id, first_name, last_name) VALUES (?, ?, 'A', ?)",
            (guest, tid, str(i)),
        )
        notes = "Booking.com #BDC-1" if i == 0 else ("Website booking" if i == 1 else None)
        conn.execute(
            """INSERT INTO hospitalityclaw_reservation
               (id, tenant_id, hotel_id, guest_id, room_id, room_type_id,
                check_in, check_out, adults, status, rate, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, '2026-10-14', '2026-10-17', 1, 'checked_in', '120.00', ?, '2026-10-10')""",
            (res, tid, hotel_id, guest, rooms[i], rt_id, notes),
        )
        conn.execute(
            """INSERT INTO hospitalityclaw_folio
               (id, tenant_id, reservation_id, guest_id, status, balance)
               VALUES (?, ?, ?, ?, 'open', '0')""",
            (folio, tid, res, guest),
        )
        conn.execute(
            """INSERT INTO hospitalityclaw_folio_charge
               (id, tenant_id, folio_id, description, amount, charge_type, posting_date)
               VALUES (?, ?, ?, 'Room tariff', '100.00', 'room', '2026-10-15')""",
            (str(uuid.uuid4()), tid, folio),
        )
        if i == 0:
            conn.execute(
                """INSERT INTO hospitalityclaw_channel_booking
                   (id, tenant_id, hotel_id, ota, external_id, reservation_id, reservation_ids)
                   VALUES (?, ?, ?, 'booking_com', 'BDC-1', ?, ?)""",
                (str(uuid.uuid4()), tid, hotel_id, res, f'["{res}"]'),
            )

    # Cancelled reservation created in the reporting window
    guest_c = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO hospitalityclaw_guest (id, tenant_id, first_name, last_name) VALUES (?, ?, 'C', 'X')",
        (guest_c, tid),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_reservation
           (id, tenant_id, hotel_id, guest_id, room_type_id,
            check_in, check_out, adults, status, rate, created_at)
           VALUES (?, ?, ?, ?, ?, '2026-10-20', '2026-10-22', 1, 'cancelled', '100.00', '2026-10-15')""",
        (str(uuid.uuid4()), tid, hotel_id, guest_c, rt_id),
    )
    conn.commit()
    conn.close()

    return {
        "tenant_id": tid,
        "hotel_id": hotel_id,
        "today": today,
    }


def test_revpar_adr_occupancy_alos_cancel(hotel):
    payload = ax.performance(
        hotel["tenant_id"],
        from_date="2026-10-15",
        to_date="2026-10-16",
        today=hotel["today"],
    )
    kpis = payload["kpis"]
    # 5 * 100 room revenue, 10 available rooms, 5 sold
    assert kpis["room_revenue"] == "500.00"
    assert kpis["rooms_sold"] == 5
    assert kpis["rooms_available"] == 10
    assert kpis["available_room_nights"] == 10
    assert kpis["occupancy_pct"] == "50.00"
    assert kpis["adr"] == "100.00"  # 500/5
    assert kpis["revpar"] == "50.00"  # 500/10
    assert float(kpis["alos"]) == 3.0  # Oct 14→17
    assert kpis["cancellations"] == 1
    assert kpis["reservations_created"] == 1
    assert kpis["cancellation_rate_pct"] == "100.00"


def test_booking_source_breakdown(hotel):
    payload = ax.performance(
        hotel["tenant_id"],
        from_date="2026-10-15",
        to_date="2026-10-16",
        today=hotel["today"],
    )
    by_key = {s["key"]: s for s in payload["booking_sources"]["sources"]}
    assert by_key["ota"]["bookings"] == 1
    assert by_key["direct_website"]["bookings"] == 1
    assert by_key["direct_walk_in"]["bookings"] == 3
    assert payload["booking_sources"]["ota_breakdown"][0]["label"] == "Booking.com"


def test_csv_and_pdf_export(hotel):
    payload = ax.performance(
        hotel["tenant_id"],
        from_date="2026-10-15",
        to_date="2026-10-16",
        today=hotel["today"],
    )
    csv_bytes = ax.export_csv(payload)
    assert b"RevPAR" in csv_bytes
    assert b"Occupancy by night" in csv_bytes
    pdf = ax.export_pdf(payload)
    assert pdf.startswith(b"%PDF")
    assert b"RevPAR" in pdf or b"Performance" in pdf


def test_branch_comparison_includes_siblings(hotel, monkeypatch):
    # Second property shares same DB with different tenant_id
    tid2 = str(uuid.uuid4())
    hotel2 = str(uuid.uuid4())
    rt2 = str(uuid.uuid4())
    room2 = str(uuid.uuid4())
    conn = connect_erp()
    abbr2 = tid2.replace("-", "")[:8]
    conn.execute(
        "INSERT INTO company (id, name, abbr) VALUES (?, ?, ?)",
        (tid2, f"Branch-{abbr2}", abbr2),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_hotel
           (id, tenant_id, name, currency) VALUES (?, ?, 'Annex', 'USD')""",
        (hotel2, tid2),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_room_type
           (id, tenant_id, hotel_id, name, code, base_rate)
           VALUES (?, ?, ?, 'Std', 'STD', '80.00')""",
        (rt2, tid2, hotel2),
    )
    conn.execute(
        """INSERT INTO hospitalityclaw_room
           (id, tenant_id, hotel_id, room_type_id, room_number, status)
           VALUES (?, ?, ?, ?, '201', 'vacant')""",
        (room2, tid2, hotel2, rt2),
    )
    conn.commit()
    conn.close()

    payload = ax.performance(
        hotel["tenant_id"],
        from_date="2026-10-15",
        to_date="2026-10-16",
        property_ids=[hotel["tenant_id"], tid2],
        today=hotel["today"],
    )
    assert len(payload["branches"]) == 2
    names = {b["hotel_name"] for b in payload["branches"]}
    assert "Harbor Inn" in names
    assert "Annex" in names
