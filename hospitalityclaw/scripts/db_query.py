#!/usr/bin/env python3
"""HospitalityClaw — hotel PMS actions. Every query is tenant-scoped.

Usage: python3 db_query.py --action <action-name> [--flags ...]
Output: JSON to stdout.
"""
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation


def _load_workspace_env():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    path_keys = {
        "ERPCLAW_HOME",
        "DATA_DIR",
        "ERPCLAW_DB_PATH",
        "WEBCLAW_DB_PATH",
        "ERPCLAW_ENGINE_PATH",
    }
    for parts in (("webclaw", "api", ".env"), ("erpclaw-engine", ".env")):
        path = os.path.join(root, *parts)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    # Expand ~; resolve relative paths against the PMS root.
                    value = os.path.expanduser(value)
                    if key in path_keys and value and not os.path.isabs(value):
                        value = os.path.abspath(os.path.join(root, value))
                    os.environ[key] = value


_load_workspace_env()

try:
    import importlib.util
    bundled = os.path.join(
        os.path.dirname(__file__), "..", "..", "erpclaw-engine",
        "scripts", "erpclaw-setup", "lib")
    if os.path.isdir(os.path.join(os.path.abspath(bundled), "erpclaw_lib")):
        sys.path.insert(0, os.path.abspath(bundled))
    elif importlib.util.find_spec("erpclaw_lib") is None:
        home = os.environ.get("ERPCLAW_HOME") or os.environ.get("DATA_DIR") or "~/.openclaw/erpclaw"
        sys.path.insert(0, os.path.join(os.path.expanduser(home), "lib"))
    from erpclaw_lib.db import get_connection
    from erpclaw_lib.response import ok, err, row_to_dict, rows_to_list
    from erpclaw_lib.audit import audit_safe as audit
    from erpclaw_lib.args import SafeArgumentParser, check_unknown_args
    from erpclaw_lib.tenant import bind_connection, resolve_tenant_id
    from erpclaw_lib.query import Q, P, Table, fn
except ImportError:
    import json as _json
    print(_json.dumps({
        "status": "error",
        "error": "ERPClaw foundation not installed. Install erpclaw first.",
    }))
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from init_db import init_db  # noqa: E402


def _money(value, field="amount") -> str:
    if value is None or str(value).strip() == "":
        return "0"
    try:
        return str(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        err(f"Invalid money value for {field}: {value}")


def _update_timestamped_entity(conn, table, fields, entity_id, tenant_id):
    """Update controlled columns with a native timestamp on SQLite/Postgres."""
    assignments = [f"{column} = ?" for column in fields]
    assignments.append("updated_at = CURRENT_TIMESTAMP")
    params = [*fields.values(), entity_id, tenant_id]
    conn.execute(
        f"UPDATE {table} SET {', '.join(assignments)} "
        "WHERE id = ? AND tenant_id = ?",
        params,
    )


def _normalize_currency(code) -> str:
    raw = str(code or "USD").strip().upper()
    return raw if len(raw) == 3 and raw.isalpha() else "USD"


def _ensure_enabled_currency(conn, code: str) -> None:
    """Make the hotel's ISO currency usable for room rates and GL postings.

    Failures are isolated to a savepoint so a missing ``currency`` table on
    minimal/test databases cannot roll back the surrounding hotel seed.
    """
    code = _normalize_currency(code)
    try:
        from erpclaw_lib.query import insert_or_ignore

        conn.execute("SAVEPOINT hclaw_currency")
        try:
            conn.execute(
                insert_or_ignore(
                    """INSERT OR IGNORE INTO currency
                       (code, name, symbol, decimal_places, enabled)
                       VALUES (?, ?, ?, 2, 1)"""
                ),
                (code, code, code),
            )
            conn.execute("UPDATE currency SET enabled = 1 WHERE code = ?", (code,))
            conn.execute("RELEASE SAVEPOINT hclaw_currency")
        except Exception:
            try:
                conn.execute("ROLLBACK TO SAVEPOINT hclaw_currency")
            except Exception:
                pass
    except Exception:
        pass


def _tenant(args) -> str:
    tid = resolve_tenant_id(
        getattr(args, "company_id", None),
        getattr(args, "tenant_id", None),
    )
    if not tid:
        err("tenant_id is required")
    return tid


def _tenant_ids(args) -> list[str]:
    """One hotel UUID, or every property under X-Property-ID=ALL."""
    from erpclaw_lib.tenant import current_tenant_ids

    tid = resolve_tenant_id(
        getattr(args, "company_id", None),
        getattr(args, "tenant_id", None),
        allow_missing=True,
    )
    if tid:
        return [tid]
    tids = current_tenant_ids()
    if tids:
        return tids
    return [_tenant(args)]


def _tenant_in_clause(args) -> tuple[str, list[str]]:
    """Return ``(placeholders, ids)`` for ``tenant_id IN (...)`` predicates."""
    tids = _tenant_ids(args)
    return ",".join("?" for _ in tids), tids


_TABLE_GET = {
    "hospitalityclaw_hotel": "SELECT * FROM hospitalityclaw_hotel WHERE id = ? AND tenant_id = ?",
    "hospitalityclaw_room_type": "SELECT * FROM hospitalityclaw_room_type WHERE id = ? AND tenant_id = ?",
    "hospitalityclaw_room": "SELECT * FROM hospitalityclaw_room WHERE id = ? AND tenant_id = ?",
    "hospitalityclaw_guest": "SELECT * FROM hospitalityclaw_guest WHERE id = ? AND tenant_id = ?",
    "hospitalityclaw_reservation": "SELECT * FROM hospitalityclaw_reservation WHERE id = ? AND tenant_id = ?",
    "hospitalityclaw_folio": "SELECT * FROM hospitalityclaw_folio WHERE id = ? AND tenant_id = ?",
    "hospitalityclaw_folio_charge": "SELECT * FROM hospitalityclaw_folio_charge WHERE id = ? AND tenant_id = ?",
    "hospitalityclaw_ledger_entry": "SELECT * FROM hospitalityclaw_ledger_entry WHERE id = ? AND tenant_id = ?",
    "hospitalityclaw_transaction": "SELECT * FROM hospitalityclaw_transaction WHERE id = ? AND tenant_id = ?",
}

def owned(conn, table: str, row_id: str, tenant_id: str, label: str):
    sql = _TABLE_GET.get(table)
    if not sql:
        err("Invalid table")
    row = conn.execute(sql, (row_id, tenant_id)).fetchone()
    if not row:
        err(f"{label} not found")
    return row


def _today_iso() -> str:
    return date.today().isoformat()


def _add_days_iso(iso: str, days: int) -> str:
    y, m, d = (int(p) for p in iso[:10].split("-"))
    return (date(y, m, d) + timedelta(days=days)).isoformat()


def _room_calendar_clash(
    conn,
    tenant_id: str,
    room_id: str,
    check_in: str,
    check_out: str,
    exclude_reservation_id: str | None = None,
):
    """Return a conflicting reservation row if the room is booked on these dates."""
    sql = """SELECT id, check_in, check_out FROM hospitalityclaw_reservation
             WHERE tenant_id = ? AND room_id = ?
               AND status IN ('reserved', 'pre_checked_in', 'checked_in')
               AND check_in < ? AND check_out > ?"""
    params: list = [tenant_id, room_id, check_out, check_in]
    if exclude_reservation_id:
        sql += " AND id != ?"
        params.append(exclude_reservation_id)
    return conn.execute(sql, params).fetchone()


def _room_available_from_value(room) -> str | None:
    if room is None:
        return None
    try:
        raw = room["available_from"]
    except (KeyError, IndexError, TypeError):
        raw = None
    if raw is None:
        return None
    text = str(raw).strip()
    return text[:10] if text else None


def _room_earliest_check_in(room, today: str | None = None) -> str | None:
    """First date this room may be checked into, based on status + schedule.

    Maintenance (out_of_order) is never bookable until marked clean again.
    Dirty rooms use available_from (or a default day-after-tomorrow).
    """
    today = today or _today_iso()
    status = room["status"]
    scheduled = _room_available_from_value(room)

    if status == "vacant":
        return today
    if status == "dirty":
        if scheduled:
            return scheduled
        # Default when no schedule set: cleaned tomorrow → bookable day after
        return _add_days_iso(today, 2)
    if status == "out_of_order":
        return None  # fully off inventory until marked clean
    if status == "occupied":
        return today  # future stays gated by calendar only
    return None


def _hk_turnaround_days(conn, tenant_id: str, hotel_id: str | None = None) -> int:
    """Nights until a dirty room is bookable again (default 2 = clean tomorrow)."""
    import json as _json
    try:
        if hotel_id:
            row = conn.execute(
                """SELECT settings_json FROM hospitalityclaw_hotel
                   WHERE id = ? AND tenant_id = ?""",
                (hotel_id, tenant_id),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT settings_json FROM hospitalityclaw_hotel
                   WHERE tenant_id = ? ORDER BY created_at LIMIT 1""",
                (tenant_id,),
            ).fetchone()
        raw = row["settings_json"] if row and hasattr(row, "keys") else (row[0] if row else None)
        data = _json.loads(raw or "{}") if isinstance(raw, str) else (raw or {})
        if not isinstance(data, dict):
            return 2
        hk = data.get("housekeeping") if isinstance(data.get("housekeeping"), dict) else {}
        payments = data.get("payments") if isinstance(data.get("payments"), dict) else {}
        days = (
            hk.get("turnaround_days")
            or hk.get("dirty_available_in_days")
            or payments.get("turnaround_days")
            or data.get("turnaround_days")
        )
        if days is None:
            return 2
        return max(1, min(14, int(days)))
    except Exception:
        return 2


def _room_bookable_for_dates(
    conn,
    tenant_id: str,
    room,
    check_in: str,
    check_out: str,
    *,
    immediate: bool = False,
    exclude_reservation_id: str | None = None,
) -> bool:
    """True if this physical room can cover [check_in, check_out)."""
    today = _today_iso()
    status = room["status"]
    same_day = immediate or check_in <= today

    if status == "out_of_order":
        return False
    if same_day and status != "vacant":
        return False
    if status == "dirty":
        earliest = _room_earliest_check_in(room, today)
        if not earliest or check_in < earliest:
            return False
    elif status not in ("vacant", "occupied"):
        return False

    return _room_calendar_clash(
        conn, tenant_id, room["id"], check_in, check_out, exclude_reservation_id,
    ) is None


def _assert_room_assignable(
    conn,
    tenant_id: str,
    room,
    check_in: str,
    check_out: str,
    *,
    immediate: bool = False,
    exclude_reservation_id: str | None = None,
):
    """Enforce housekeeping schedule + calendar for a concrete room assignment."""
    today = _today_iso()
    status = room["status"]
    number = room["room_number"]
    same_day = immediate or check_in <= today

    if status == "out_of_order":
        err(
            f"Room {number} is in maintenance and unavailable for booking",
            suggestion="Mark it clean when work is finished, or pick another room.",
        )

    if same_day and status != "vacant":
        err(
            f"Room {number} must be vacant and clean for same-day assignment",
            suggestion="Pick a clean vacant room, or book for a later date.",
        )

    if status == "dirty":
        earliest = _room_earliest_check_in(room, today)
        if not earliest or check_in < earliest:
            err(
                f"Room {number} is dirty until {earliest or 'cleaning is scheduled'} "
                f"(earliest check-in {earliest or 'unknown'})",
                suggestion="Choose a later check-in, or pick another room.",
            )
    elif status not in ("vacant", "occupied"):
        err(f"Room {number} is not available (status: {status})")

    clash = _room_calendar_clash(
        conn, tenant_id, room["id"], check_in, check_out, exclude_reservation_id,
    )
    if clash:
        err(
            f"Room {number} is already booked "
            f"{clash['check_in']} → {clash['check_out']}",
            suggestion="Pick another room or different dates.",
        )


def _assert_room_type_capacity(
    conn,
    tenant_id: str,
    room_type_id: str,
    check_in: str,
    check_out: str,
    *,
    immediate: bool = False,
    exclude_reservation_id: str | None = None,
    preferred_room_id: str | None = None,
    hotel_id: str | None = None,
):
    """Refuse booking when no room of this type can cover the stay dates.

    Also enforces per-night sellable inventory (physical bookable minus
    unassigned holds) so front-desk bookings cannot overbook vs OTA math.
    """
    rooms = conn.execute(
        """SELECT * FROM hospitalityclaw_room
           WHERE tenant_id = ? AND room_type_id = ?""",
        (tenant_id, room_type_id),
    ).fetchall()
    if not rooms:
        err(
            "No rooms exist for this room type",
            suggestion="Add a room in Inventory before booking.",
        )

    bookable = [
        rm for rm in rooms
        if _room_bookable_for_dates(
            conn, tenant_id, rm, check_in, check_out,
            immediate=immediate,
            exclude_reservation_id=exclude_reservation_id,
        )
    ]
    if preferred_room_id:
        if any(rm["id"] == preferred_room_id for rm in bookable):
            pass  # still check nightly sellable below
        else:
            preferred = next((rm for rm in rooms if rm["id"] == preferred_room_id), None)
            if preferred:
                _assert_room_assignable(
                    conn, tenant_id, preferred, check_in, check_out,
                    immediate=immediate,
                    exclude_reservation_id=exclude_reservation_id,
                )
            err("Selected room is not available for these dates")
    elif not bookable:
        err(
            "No rooms of this type are available for these dates "
            "(cleaning schedule, maintenance, or existing bookings)",
            suggestion="Pick later dates, wait for cleaning, or choose another room type.",
        )

    # Per-night sellable (align with channel availability / stop-sell).
    hid = hotel_id or (rooms[0]["hotel_id"] if rooms else None)
    if hid:
        try:
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            api = os.path.join(root, "webclaw", "api")
            if api not in sys.path:
                sys.path.insert(0, api)
            from services.inventory_controls import nightly_sellable
        except Exception:
            nightly_sellable = None
        if nightly_sellable is not None:
            cursor = date.fromisoformat(check_in[:10])
            end = date.fromisoformat(check_out[:10])
            while cursor < end:
                left = nightly_sellable(
                    conn, tenant_id, hid, room_type_id, cursor.isoformat(),
                    exclude_reservation_id=exclude_reservation_id,
                )
                if left < 1:
                    err(
                        f"No sellable inventory left on {cursor.isoformat()} "
                        f"for this room type (sold out or stop-sell)",
                        suggestion="Pick other dates, another room type, or clear a stop-sell.",
                    )
                cursor += timedelta(days=1)


def _parse_int(value, default):
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _page(args, default_limit=200):
    """WebClaw entity lists always send --limit / --offset."""
    limit = _parse_int(getattr(args, "limit", None), default_limit)
    offset = max(0, _parse_int(getattr(args, "offset", None), 0))
    return limit, offset


def _ok_paged(conn, key, sql, params, args, order_by, extra=None, default_limit=200):
    total = conn.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    limit, offset = _page(args, default_limit)
    if limit <= 0:
        rows = []
        has_more = total > offset
    else:
        rows = conn.execute(
            f"{sql} ORDER BY {order_by} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        has_more = offset + len(rows) < total
    payload = {
        key: rows_to_list(rows),
        "total_count": total,
        "has_more": has_more,
    }
    if extra:
        payload.update(extra)
    ok(payload)


def hospitality_initialize_schema(conn, args):
    init_db(conn)
    ok({"message": "Hospitality schema ready"})


def hospitality_seed_defaults(conn, args):
    tenant_id = _tenant(args)
    bind_connection(conn, tenant_id)
    init_db(conn)

    hotel_name = args.hotel_name or "Hotel"
    currency = _normalize_currency(args.currency)
    timezone_name = args.timezone or "UTC"
    country = args.country or "United States"
    existing = conn.execute(
        "SELECT id FROM hospitalityclaw_hotel WHERE tenant_id = ? LIMIT 1",
        (tenant_id,),
    ).fetchone()
    address = getattr(args, "address", None)
    if existing:
        hotel_id = existing["id"]
        fields, params = [], []
        if args.hotel_name:
            fields.append("name = ?")
            params.append(hotel_name)
        if args.timezone:
            fields.append("timezone = ?")
            params.append(timezone_name)
        if args.currency:
            fields.append("currency = ?")
            params.append(currency)
        if args.country:
            fields.append("country = ?")
            params.append(country)
        if address is not None:
            fields.append("address = ?")
            params.append(address)
        if fields:
            fields.append("updated_at = CURRENT_TIMESTAMP")
            params.extend([hotel_id, tenant_id])
            conn.execute(
                f"UPDATE hospitalityclaw_hotel SET {', '.join(fields)} WHERE id = ? AND tenant_id = ?",
                params,
            )
    else:
        hotel_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO hospitalityclaw_hotel
               (id, tenant_id, name, timezone, currency, country, address)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (hotel_id, tenant_id, hotel_name, timezone_name, currency, country, address),
        )
    stored = conn.execute(
        "SELECT currency FROM hospitalityclaw_hotel WHERE id = ?", (hotel_id,)
    ).fetchone()
    _ensure_enabled_currency(conn, stored["currency"] if stored else currency)

    default_types = (
        ("Standard", "STD", "100.00", 2),
        ("Deluxe", "DLX", "180.00", 3),
        ("Suite", "STE", "280.00", 4),
    )
    created_types = []
    for name, code, rate, occ in default_types:
        row = conn.execute(
            "SELECT id FROM hospitalityclaw_room_type WHERE tenant_id = ? AND hotel_id = ? AND code = ?",
            (tenant_id, hotel_id, code),
        ).fetchone()
        if row:
            created_types.append(row["id"])
            continue
        rtid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO hospitalityclaw_room_type
               (id, tenant_id, hotel_id, name, code, base_rate, max_occupancy)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (rtid, tenant_id, hotel_id, name, code, rate, occ),
        )
        created_types.append(rtid)

    audit(conn, "hospitalityclaw", "seed", "hotel", hotel_id,
          description=f"Seeded hospitality defaults for {hotel_name}")

    tax = conn.execute(
        "SELECT id FROM hospitalityclaw_tax_rule WHERE tenant_id = ? AND hotel_id = ? LIMIT 1",
        (tenant_id, hotel_id),
    ).fetchone()
    if not tax:
        conn.execute(
            """INSERT INTO hospitalityclaw_tax_rule
               (id, tenant_id, hotel_id, name, rate_percent, applies_to, is_default, is_active)
               VALUES (?, ?, ?, 'Occupancy Tax', '10', 'room', 1, 1)""",
            (str(uuid.uuid4()), tenant_id, hotel_id),
        )

    conn.commit()
    ok({
        "hotel_id": hotel_id,
        "room_type_ids": created_types,
        "tenant_id": tenant_id,
    })


def hospitality_status(conn, args):
    """Headline counts for the hotel dashboard.

    Optional ``from_date`` / ``to_date`` (ISO, ``to_date`` exclusive) scopes
    reservations, guests, charges, and revenue. Open folios / outstanding stay
    point-in-time. Without dates, reservations/guests are active ops only.
    """
    init_db(conn)
    ph, tids = _tenant_in_clause(args)
    tenant_id = tids[0] if len(tids) == 1 else None
    from_date = getattr(args, "from_date", None)
    to_date = getattr(args, "to_date", None)

    rooms = conn.execute(
        f"SELECT COUNT(*) AS cnt FROM hospitalityclaw_room WHERE tenant_id IN ({ph})",
        tids,
    ).fetchone()["cnt"]

    if from_date or to_date:
        res_sql = f"""SELECT COUNT(*) AS cnt FROM hospitalityclaw_reservation
                     WHERE tenant_id IN ({ph}) AND status NOT IN ('cancelled')"""
        res_params: list = list(tids)
        guest_sql = f"""SELECT COUNT(DISTINCT guest_id) AS cnt FROM hospitalityclaw_reservation
                       WHERE tenant_id IN ({ph}) AND status NOT IN ('cancelled')"""
        guest_params: list = list(tids)
        if from_date:
            res_sql += " AND check_out > ?"
            res_params.append(from_date)
            guest_sql += " AND check_out > ?"
            guest_params.append(from_date)
        if to_date:
            res_sql += " AND check_in < ?"
            res_params.append(to_date)
            guest_sql += " AND check_in < ?"
            guest_params.append(to_date)
        reservations = conn.execute(res_sql, res_params).fetchone()["cnt"]
        guests = conn.execute(guest_sql, guest_params).fetchone()["cnt"]
    else:
        reservations = conn.execute(
            f"""SELECT COUNT(*) AS cnt FROM hospitalityclaw_reservation
               WHERE tenant_id IN ({ph}) AND status IN ('reserved', 'pre_checked_in', 'checked_in')""",
            tids,
        ).fetchone()["cnt"]
        guests = conn.execute(
            f"""SELECT COUNT(DISTINCT guest_id) AS cnt FROM hospitalityclaw_reservation
               WHERE tenant_id IN ({ph}) AND status IN ('reserved', 'pre_checked_in', 'checked_in')""",
            tids,
        ).fetchone()["cnt"]

    folios = conn.execute(
        f"""SELECT COUNT(*) AS cnt FROM hospitalityclaw_folio
           WHERE tenant_id IN ({ph}) AND status = 'open'""",
        tids,
    ).fetchone()["cnt"]
    hotels = conn.execute(
        f"SELECT COUNT(*) AS cnt FROM hospitalityclaw_hotel WHERE tenant_id IN ({ph})",
        tids,
    ).fetchone()["cnt"]

    rev_sql = f"""SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) AS total
                 FROM hospitalityclaw_transaction
                 WHERE tenant_id IN ({ph}) AND status = 'posted'"""
    rev_params: list = list(tids)
    if from_date:
        rev_sql += " AND date(COALESCE(posted_at, created_at)) >= date(?)"
        rev_params.append(from_date)
    if to_date:
        rev_sql += " AND date(COALESCE(posted_at, created_at)) < date(?)"
        rev_params.append(to_date)
    revenue = conn.execute(rev_sql, rev_params).fetchone()["total"]

    charge_sql = f"""SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) AS total
                    FROM hospitalityclaw_folio_charge
                    WHERE tenant_id IN ({ph}) AND charge_type != 'payment'"""
    charge_params: list = list(tids)
    if from_date:
        charge_sql += " AND posting_date >= ?"
        charge_params.append(from_date)
    if to_date:
        charge_sql += " AND posting_date < ?"
        charge_params.append(to_date)
    charges_total = conn.execute(charge_sql, charge_params).fetchone()["total"]

    outstanding_total = conn.execute(
        f"""SELECT COALESCE(SUM(CAST(balance AS REAL)), 0) AS total
           FROM hospitalityclaw_folio
           WHERE tenant_id IN ({ph}) AND status = 'open'""",
        tids,
    ).fetchone()["total"]
    hotel = conn.execute(
        f"SELECT currency FROM hospitalityclaw_hotel WHERE tenant_id IN ({ph}) ORDER BY name LIMIT 1",
        tids,
    ).fetchone()
    ok({
        "tenant_id": tenant_id,
        "tenant_ids": tids,
        "hotels": hotels,
        "rooms": rooms,
        "reservations": reservations,
        "guests": guests,
        "folios": folios,
        "revenue": str(revenue),
        "charges_total": str(charges_total),
        "outstanding_total": str(outstanding_total),
        "currency": (hotel["currency"] if hotel else None) or "USD",
        "from_date": from_date,
        "to_date": to_date,
    })


def hospitality_list_revenue(conn, args):
    """Owner/admin revenue report: bookings with charges and posted payments.

    Avoid SELECT-list subqueries with FROM — the tenant SQL rewriter mistakes
    the inner FROM for the primary table and injects a broken outer predicate.
    """
    init_db(conn)
    ph, tids = _tenant_in_clause(args)
    hotel = conn.execute(
        f"SELECT currency FROM hospitalityclaw_hotel WHERE tenant_id IN ({ph}) ORDER BY name LIMIT 1",
        tids,
    ).fetchone()
    currency = (hotel["currency"] if hotel else None) or "USD"

    from_date = getattr(args, "from_date", None)
    to_date = getattr(args, "to_date", None)

    sql = f"""SELECT r.id AS reservation_id, r.status AS reservation_status,
                    r.check_in, r.check_out, r.rate AS room_rate, r.hotel_id,
                    g.first_name, g.last_name, g.email AS guest_email,
                    rt.name AS room_type_name, rm.room_number,
                    f.id AS folio_id, f.status AS folio_status, f.balance AS folio_balance
             FROM hospitalityclaw_reservation r
             JOIN hospitalityclaw_guest g ON g.id = r.guest_id
             JOIN hospitalityclaw_room_type rt ON rt.id = r.room_type_id
             LEFT JOIN hospitalityclaw_room rm ON rm.id = r.room_id
             LEFT JOIN hospitalityclaw_folio f
               ON f.reservation_id = r.id AND f.tenant_id = r.tenant_id
             WHERE r.tenant_id IN ({ph})"""
    params: list = list(tids)
    # Optional stay-window filter (all-time when omitted).
    if from_date:
        sql += " AND r.check_out > ?"
        params.append(from_date)
    if to_date:
        sql += " AND r.check_in < ?"
        params.append(to_date)
    sql += " ORDER BY r.check_in DESC"

    limit = int(getattr(args, "limit", None) or 200)
    offset = int(getattr(args, "offset", None) or 0)
    if limit < 0:
        limit = 200
    # Inline LIMIT/OFFSET — avoids mixing bound params with tenant rewriter inserts.
    rows = conn.execute(f"{sql} LIMIT {int(limit)} OFFSET {int(offset)}", params).fetchall()

    folio_ids = [r["folio_id"] for r in rows if r["folio_id"]]
    charges_by_folio: dict[str, Decimal] = {}
    payments_by_folio: dict[str, list] = {fid: [] for fid in folio_ids}
    payments_total_by_folio: dict[str, Decimal] = {}

    if folio_ids:
        placeholders = ",".join("?" * len(folio_ids))
        for c in conn.execute(
            f"""SELECT folio_id, COALESCE(SUM(CAST(amount AS REAL)), 0) AS total
                FROM hospitalityclaw_folio_charge
                WHERE tenant_id IN ({ph}) AND charge_type != 'payment'
                  AND folio_id IN ({placeholders})
                GROUP BY folio_id""",
            (*tids, *folio_ids),
        ).fetchall():
            charges_by_folio[c["folio_id"]] = Decimal(str(c["total"] or 0))

        for p in conn.execute(
            f"""SELECT id, folio_id, amount, payment_method, status,
                       posted_at, reference, created_at
                FROM hospitalityclaw_transaction
                WHERE tenant_id IN ({ph}) AND status = 'posted'
                  AND folio_id IN ({placeholders})
                ORDER BY COALESCE(posted_at, created_at) DESC""",
            (*tids, *folio_ids),
        ).fetchall():
            fid = p["folio_id"]
            amt = Decimal(str(p["amount"] or 0))
            payments_total_by_folio[fid] = payments_total_by_folio.get(fid, Decimal("0")) + amt
            payments_by_folio.setdefault(fid, []).append({
                "id": p["id"],
                "amount": str(p["amount"]),
                "payment_method": p["payment_method"],
                "status": p["status"],
                "posted_at": p["posted_at"] or p["created_at"],
                "reference": p["reference"],
                "description": "Payment",
            })

    bookings = []
    for r in rows:
        fid = r["folio_id"]
        charges = charges_by_folio.get(fid, Decimal("0")) if fid else Decimal("0")
        payments = payments_total_by_folio.get(fid, Decimal("0")) if fid else Decimal("0")
        bookings.append({
            "reservation_id": r["reservation_id"],
            "reservation_status": r["reservation_status"],
            "check_in": r["check_in"],
            "check_out": r["check_out"],
            "room_rate": str(r["room_rate"] or "0"),
            "first_name": r["first_name"],
            "last_name": r["last_name"],
            "guest_email": r["guest_email"],
            "room_type_name": r["room_type_name"],
            "room_number": r["room_number"],
            "folio_id": fid,
            "folio_status": r["folio_status"],
            "folio_balance": str(r["folio_balance"] or "0") if fid else "0",
            "charges_total": str(charges),
            "payments_total": str(payments),
            "payments": payments_by_folio.get(fid or "", []),
        })

    revenue_total = conn.execute(
        f"""SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) AS total
           FROM hospitalityclaw_transaction
           WHERE tenant_id IN ({ph}) AND status = 'posted'""",
        tids,
    ).fetchone()["total"]
    charges_total = conn.execute(
        f"""SELECT COALESCE(SUM(CAST(amount AS REAL)), 0) AS total
           FROM hospitalityclaw_folio_charge
           WHERE tenant_id IN ({ph}) AND charge_type != 'payment'""",
        tids,
    ).fetchone()["total"]
    outstanding_total = conn.execute(
        f"""SELECT COALESCE(SUM(CAST(balance AS REAL)), 0) AS total
           FROM hospitalityclaw_folio
           WHERE tenant_id IN ({ph}) AND status = 'open'""",
        tids,
    ).fetchone()["total"]

    ok({
        "currency": currency,
        "revenue_total": str(revenue_total),
        "charges_total": str(charges_total),
        "outstanding_total": str(outstanding_total),
        "bookings": bookings,
        "total_count": len(bookings),
    })


def hospitality_list_hotels(conn, args):
    tenant_id = _tenant(args)
    _ok_paged(
        conn, "hotels",
        "SELECT * FROM hospitalityclaw_hotel WHERE tenant_id = ?",
        [tenant_id], args, "name", extra={"tenant_id": tenant_id},
    )


def hospitality_get_hotel(conn, args):
    tenant_id = _tenant(args)
    hotel_id = args.id or args.hotel_id
    if not hotel_id:
        err("--id is required")
    row = owned(conn, "hospitalityclaw_hotel", hotel_id, tenant_id, "Hotel")
    ok({"hotel": row_to_dict(row)})


def hospitality_update_hotel(conn, args):
    tenant_id = _tenant(args)
    hotel_id = args.id or args.hotel_id
    if not hotel_id:
        err("--id is required")
    row = owned(conn, "hospitalityclaw_hotel", hotel_id, tenant_id, "Hotel")
    fields = {}
    for col in ("name", "timezone", "currency", "country", "address", "phone", "email"):
        val = getattr(args, col, None)
        if val is not None:
            fields[col] = val

    # Merge property policies / payment keys into settings_json without wiping unknowns.
    settings_patch = {}
    raw_settings = getattr(args, "settings_json", None)
    if raw_settings:
        try:
            import json as _json
            parsed = _json.loads(raw_settings) if isinstance(raw_settings, str) else raw_settings
            if isinstance(parsed, dict):
                settings_patch.update(parsed)
        except Exception:
            err("settings_json must be valid JSON object")
    for key in (
        "check_in_time", "check_out_time", "cancellation_policy",
        "payment_gateway", "payment_publishable_key", "payment_secret_key",
        "payment_webhook_secret",
    ):
        val = getattr(args, key, None)
        if val is not None:
            settings_patch[key] = val
    if settings_patch:
        import json as _json
        current = {}
        raw = row["settings_json"] if hasattr(row, "keys") else None
        if raw:
            try:
                current = _json.loads(raw) if isinstance(raw, str) else dict(raw)
            except Exception:
                current = {}
        current.update({k: v for k, v in settings_patch.items() if v is not None})
        fields["settings_json"] = _json.dumps(current)

    if not fields:
        err("No fields to update")
    _update_timestamped_entity(
        conn, "hospitalityclaw_hotel", fields, hotel_id, tenant_id
    )
    conn.commit()
    ok({"hotel_id": hotel_id, "updated_fields": list(fields.keys())})


def hospitality_add_room_type(conn, args):
    tenant_id = _tenant(args)
    hotel_id = args.hotel_id
    if not hotel_id or not args.name or not args.code:
        err("--hotel-id, --name and --code are required")
    owned(conn, "hospitalityclaw_hotel", hotel_id, tenant_id, "Hotel")
    rtid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_room_type
           (id, tenant_id, hotel_id, name, code, base_rate, max_occupancy, description)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            rtid, tenant_id, hotel_id, args.name, args.code.upper(),
            _money(args.base_rate, "base_rate"),
            int(args.max_occupancy or 2),
            args.description,
        ),
    )
    conn.commit()
    ok({"room_type_id": rtid})


def hospitality_list_room_types(conn, args):
    tenant_id = _tenant(args)
    sql = """SELECT rt.*, h.name AS hotel_name
             FROM hospitalityclaw_room_type rt
             JOIN hospitalityclaw_hotel h ON h.id = rt.hotel_id
             WHERE rt.tenant_id = ?"""
    params = [tenant_id]
    if args.hotel_id:
        sql += " AND rt.hotel_id = ?"
        params.append(args.hotel_id)
    _ok_paged(conn, "room_types", sql, params, args, "rt.code")


def hospitality_get_room_type(conn, args):
    tenant_id = _tenant(args)
    rtid = args.id or args.room_type_id
    if not rtid:
        err("--id is required")
    row = owned(conn, "hospitalityclaw_room_type", rtid, tenant_id, "Room type")
    ok({"room_type": row_to_dict(row)})


def hospitality_update_room_type(conn, args):
    tenant_id = _tenant(args)
    rtid = args.id or args.room_type_id
    if not rtid:
        err("--id is required")
    owned(conn, "hospitalityclaw_room_type", rtid, tenant_id, "Room type")
    fields = {}
    for col in ("name", "code", "base_rate", "max_occupancy", "description"):
        val = getattr(args, col, None)
        if val is None:
            continue
        if col == "code":
            fields[col] = str(val).upper()
        elif col == "base_rate":
            fields[col] = _money(val, "base_rate")
        elif col == "max_occupancy":
            fields[col] = int(val)
        else:
            fields[col] = val
    if not fields:
        err("No fields to update")
    _update_timestamped_entity(
        conn, "hospitalityclaw_room_type", fields, rtid, tenant_id
    )
    conn.commit()
    ok({"room_type_id": rtid, "updated_fields": list(fields.keys())})


def hospitality_delete_room_type(conn, args):
    tenant_id = _tenant(args)
    rtid = args.id or args.room_type_id
    if not rtid:
        err("--id is required")
    owned(conn, "hospitalityclaw_room_type", rtid, tenant_id, "Room type")
    rooms = conn.execute(
        "SELECT COUNT(*) AS cnt FROM hospitalityclaw_room WHERE tenant_id = ? AND room_type_id = ?",
        (tenant_id, rtid),
    ).fetchone()["cnt"]
    if rooms:
        err(f"Cannot delete room type: {rooms} room(s) still use it")
    conn.execute(
        "DELETE FROM hospitalityclaw_room_type WHERE id = ? AND tenant_id = ?",
        (rtid, tenant_id),
    )
    conn.commit()
    ok({"room_type_id": rtid, "deleted": True})


def hospitality_add_room(conn, args):
    tenant_id = _tenant(args)
    if not args.hotel_id or not args.room_type_id or not args.room_number:
        err("--hotel-id, --room-type-id and --room-number are required")
    owned(conn, "hospitalityclaw_hotel", args.hotel_id, tenant_id, "Hotel")
    owned(conn, "hospitalityclaw_room_type", args.room_type_id, tenant_id, "Room type")
    rid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_room
           (id, tenant_id, hotel_id, room_type_id, room_number, floor, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            rid, tenant_id, args.hotel_id, args.room_type_id,
            args.room_number, args.floor,
            args.status or "vacant",
        ),
    )
    conn.commit()
    ok({"room_id": rid})


def hospitality_list_rooms(conn, args):
    init_db(conn)
    tenant_id = _tenant(args)
    sql = """SELECT rm.*, rt.name AS room_type_name, rt.code AS room_type_code,
                    rt.base_rate AS room_type_rate
             FROM hospitalityclaw_room rm
             JOIN hospitalityclaw_room_type rt ON rt.id = rm.room_type_id
             WHERE rm.tenant_id = ?"""
    params = [tenant_id]
    if args.hotel_id:
        sql += " AND rm.hotel_id = ?"
        params.append(args.hotel_id)
    if args.status:
        sql += " AND rm.status = ?"
        params.append(args.status)
    _ok_paged(conn, "rooms", sql, params, args, "rm.room_number")


def hospitality_get_room(conn, args):
    tenant_id = _tenant(args)
    rid = args.id or args.room_id
    if not rid:
        err("--id is required")
    row = owned(conn, "hospitalityclaw_room", rid, tenant_id, "Room")
    ok({"room": row_to_dict(row)})


def hospitality_update_room(conn, args):
    tenant_id = _tenant(args)
    rid = args.id or args.room_id
    if not rid:
        err("--id is required")
    owned(conn, "hospitalityclaw_room", rid, tenant_id, "Room")
    fields = {}
    if args.room_number is not None:
        fields["room_number"] = args.room_number
    if args.floor is not None:
        fields["floor"] = args.floor
    if args.room_type_id:
        owned(conn, "hospitalityclaw_room_type", args.room_type_id, tenant_id, "Room type")
        fields["room_type_id"] = args.room_type_id
    if args.status:
        raw = str(args.status).strip().lower()
        status = _ROOM_STATUS_ALIASES.get(raw, raw)
        if status not in ("vacant", "occupied", "dirty", "out_of_order"):
            err("status must be vacant, occupied, dirty, or out_of_order")
        fields["status"] = status
    if not fields:
        err("No fields to update")
    _update_timestamped_entity(
        conn, "hospitalityclaw_room", fields, rid, tenant_id
    )
    conn.commit()
    ok({"room_id": rid, "updated_fields": list(fields.keys())})


def hospitality_delete_room(conn, args):
    tenant_id = _tenant(args)
    rid = args.id or args.room_id
    if not rid:
        err("--id is required")
    owned(conn, "hospitalityclaw_room", rid, tenant_id, "Room")
    active = conn.execute(
        """SELECT COUNT(*) AS cnt FROM hospitalityclaw_reservation
           WHERE tenant_id = ? AND room_id = ? AND status IN ('reserved', 'pre_checked_in', 'checked_in')""",
        (tenant_id, rid),
    ).fetchone()["cnt"]
    if active:
        err(f"Cannot delete room: {active} active reservation(s) assigned to it")
    conn.execute(
        "DELETE FROM hospitalityclaw_room WHERE id = ? AND tenant_id = ?",
        (rid, tenant_id),
    )
    conn.commit()
    ok({"room_id": rid, "deleted": True})


_ROOM_STATUS_ALIASES = {
    "clean": "vacant",
    "vacant": "vacant",
    "dirty": "dirty",
    "maintenance": "out_of_order",
    "out_of_order": "out_of_order",
}


def hospitality_update_room_status(conn, args):
    """Housekeeping: set room Clean / Dirty / Maintenance (mapped to schema values).

    Dirty may include a schedule:
    - --cleaning-date: day cleaning finishes → available_from = next day
    - --available-from: first bookable check-in date (overrides cleaning-date)
    Maintenance takes the room fully off inventory (no schedule).
    Clean clears available_from.
    """
    init_db(conn)
    tenant_id = _tenant(args)
    rid = args.id or args.room_id
    if not rid:
        err("--id is required")
    raw = (args.status or "").strip().lower()
    status = _ROOM_STATUS_ALIASES.get(raw)
    if not status:
        err("status must be clean, dirty, or maintenance (or vacant / out_of_order)")
    owned(conn, "hospitalityclaw_room", rid, tenant_id, "Room")

    available_from = None
    if status == "vacant":
        available_from = None
    elif status == "out_of_order":
        # Fully unavailable until marked clean — no return date.
        available_from = None
    elif status == "dirty":
        explicit = getattr(args, "available_from", None)
        cleaning = getattr(args, "cleaning_date", None)
        if explicit and str(explicit).strip():
            available_from = str(explicit).strip()[:10]
        elif cleaning and str(cleaning).strip():
            available_from = _add_days_iso(str(cleaning).strip()[:10], 1)
        else:
            room_row = conn.execute(
                "SELECT hotel_id FROM hospitalityclaw_room WHERE id = ?", (rid,),
            ).fetchone()
            hid = room_row["hotel_id"] if room_row and hasattr(room_row, "keys") else None
            turnaround = _hk_turnaround_days(conn, tenant_id, hid)
            available_from = _add_days_iso(_today_iso(), turnaround)

    conn.execute(
        """UPDATE hospitalityclaw_room
           SET status = ?, available_from = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = ? AND tenant_id = ?""",
        (status, available_from, rid, tenant_id),
    )
    conn.commit()
    ok({
        "room_id": rid,
        "room_status": status,
        "available_from": available_from,
        "requested": raw,
    })


def hospitality_add_guest(conn, args):
    tenant_id = _tenant(args)
    if not args.first_name or not args.last_name:
        err("--first-name and --last-name are required")
    gid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_guest
           (id, tenant_id, first_name, last_name, email, phone, nationality, id_number, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            gid, tenant_id, args.first_name, args.last_name,
            args.email, args.phone, args.nationality, args.id_number, args.notes,
        ),
    )
    conn.commit()
    ok({"guest_id": gid})


def hospitality_list_guests(conn, args):
    tenant_id = _tenant(args)
    sql = "SELECT * FROM hospitalityclaw_guest WHERE tenant_id = ?"
    params = [tenant_id]
    if args.email:
        sql += " AND email = ?"
        params.append(args.email)
    search = getattr(args, "search", None)
    if search:
        like = f"%{search}%"
        sql += " AND (first_name LIKE ? OR last_name LIKE ? OR COALESCE(email,'') LIKE ? OR COALESCE(phone,'') LIKE ?)"
        params.extend([like, like, like, like])
    _ok_paged(conn, "guests", sql, params, args, "last_name, first_name")


def hospitality_get_guest(conn, args):
    tenant_id = _tenant(args)
    gid = args.id or args.guest_id
    if not gid:
        err("--id is required")
    row = owned(conn, "hospitalityclaw_guest", gid, tenant_id, "Guest")
    ok({"guest": row_to_dict(row)})


def _open_folio(conn, tenant_id, reservation_id, guest_id) -> str:
    existing = conn.execute(
        "SELECT id FROM hospitalityclaw_folio WHERE tenant_id = ? AND reservation_id = ?",
        (tenant_id, reservation_id),
    ).fetchone()
    if existing:
        return existing["id"]
    folio_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_folio
           (id, tenant_id, reservation_id, guest_id, status, balance)
           VALUES (?, ?, ?, ?, 'open', '0')""",
        (folio_id, tenant_id, reservation_id, guest_id),
    )
    return folio_id


def _ensure_ledger_source(conn) -> None:
    from init_db import _table_columns

    cols = _table_columns(conn, "hospitalityclaw_ledger_entry")
    if cols and "source" not in cols:
        try:
            conn.execute("ALTER TABLE hospitalityclaw_ledger_entry ADD COLUMN source TEXT")
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass


def _post_ledger(conn, tenant_id, folio_id, debit, credit, description, posting_date, source=None, account_code=None, channel_source=None):
    _ensure_ledger_source(conn)
    conn.execute(
        """INSERT INTO hospitalityclaw_ledger_entry
           (id, tenant_id, folio_id, debit, credit, description, posting_date, source_charge_id, account_code, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), tenant_id, folio_id, debit, credit, description, posting_date,
            source, account_code, channel_source,
        ),
    )


def _try_post_erp_gl(conn, tenant_id, voucher_id, amount, description, posting_date,
                     *, kind="room", channel_source=None):
    """Best-effort double-entry post into erpclaw-engine GL tables.

    kind=room  → Debit AR / Credit Income
    kind=payment → Debit Cash / Credit AR
    kind=tax → Debit AR / Credit tax liability (falls back to income)
    """
    try:
        from init_db import _is_postgres

        if _is_postgres(conn):
            tables = {
                r[0]
                for r in conn.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = current_schema()
                    """
                ).fetchall()
            }
        else:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        if not {"gl_entry", "account", "company"} <= tables:
            return None
        company = conn.execute(
            """SELECT default_receivable_account_id, default_income_account_id,
                      default_cash_account_id, default_currency
               FROM company WHERE id = ?""",
            (tenant_id,),
        ).fetchone()
        if not company:
            return None
        ar_id = company["default_receivable_account_id"]
        income_id = company["default_income_account_id"]
        cash_id = company["default_cash_account_id"] or company["default_income_account_id"]
        if not ar_id:
            return None
        hotel_currency = _normalize_currency(company["default_currency"])
        _ensure_enabled_currency(conn, hotel_currency)
        from erpclaw_lib.gl_posting import insert_gl_entries
        amt = str(Decimal(str(amount)))
        remarks = description
        if channel_source:
            remarks = f"{description} [source: {channel_source}]"
        if kind == "payment":
            if not cash_id:
                return None
            lines = [
                {"account_id": cash_id, "debit": amt, "credit": "0",
                 "remarks": remarks, "currency": hotel_currency},
                {"account_id": ar_id, "debit": "0", "credit": amt,
                 "remarks": remarks, "currency": hotel_currency},
            ]
        else:
            credit_id = income_id
            if not credit_id:
                return None
            lines = [
                {"account_id": ar_id, "debit": amt, "credit": "0",
                 "remarks": remarks, "currency": hotel_currency},
                {"account_id": credit_id, "debit": "0", "credit": amt,
                 "remarks": remarks, "currency": hotel_currency},
            ]
        return insert_gl_entries(
            conn,
            lines,
            voucher_type="journal_entry",
            voucher_id=voucher_id,
            posting_date=posting_date,
            company_id=tenant_id,
            remarks=remarks,
        )
    except Exception:
        return None


def _post_folio_line(conn, tenant_id, folio_id, description, amount, charge_type, posting_date,
                     payment_method=None, reference=None, channel_source=None):
    """Insert a folio charge, guest-ledger row, and (for room/tax/payment) ERP GL."""
    amount = Decimal(_money(str(amount), "amount"))
    charge_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_folio_charge
           (id, tenant_id, folio_id, description, amount, charge_type, posting_date)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (charge_id, tenant_id, folio_id, description, str(amount), charge_type, posting_date),
    )
    gl_ids = None
    if charge_type == "payment":
        _post_ledger(
            conn, tenant_id, folio_id, "0", str(amount), description, posting_date,
            charge_id, account_code="CASH", channel_source=channel_source,
        )
        _bump_folio_balance(conn, tenant_id, folio_id, -amount)
        conn.execute(
            """INSERT INTO hospitalityclaw_transaction
               (id, tenant_id, folio_id, amount, payment_method, status, posted_at, reference)
               VALUES (?, ?, ?, ?, ?, 'posted', ?, ?)""",
            (
                str(uuid.uuid4()), tenant_id, folio_id, str(amount),
                payment_method or "cash",
                datetime.now(timezone.utc).isoformat(),
                reference,
            ),
        )
        gl_ids = _try_post_erp_gl(
            conn, tenant_id, charge_id, amount, description, posting_date, kind="payment",
            channel_source=channel_source,
        )
    else:
        account_code = {
            "room": "ROOM_REV",
            "tax": "TAX",
            "fnb": "FNB",
        }.get(charge_type, "AR")
        _post_ledger(
            conn, tenant_id, folio_id, str(amount), "0", description, posting_date,
            charge_id, account_code=account_code, channel_source=channel_source,
        )
        _bump_folio_balance(conn, tenant_id, folio_id, amount)
        if charge_type in ("room", "tax", "fnb"):
            gl_ids = _try_post_erp_gl(
                conn, tenant_id, charge_id, amount, description, posting_date,
                kind="tax" if charge_type == "tax" else "room",
                channel_source=channel_source,
            )
    return charge_id, gl_ids


def _hotel_id_for_folio(conn, tenant_id, folio_id) -> str | None:
    row = conn.execute(
        """SELECT r.hotel_id
           FROM hospitalityclaw_folio f
           JOIN hospitalityclaw_reservation r ON r.id = f.reservation_id
           WHERE f.id = ? AND f.tenant_id = ?""",
        (folio_id, tenant_id),
    ).fetchone()
    return row["hotel_id"] if row else None


def _lookup_rate_for_night(conn, tenant_id, hotel_id, room_type_id, posting_date, fallback):
    """Pick the active rate window for one night.

    Precedence: room-type-specific plans beat hotel-wide plans, then higher
    priority, then newer / more specific start dates. Falls back to ``fallback``
    (typically the room-type base rate).
    """
    try:
        row = conn.execute(
            """SELECT id, name, code, nightly_rate, start_date, end_date, priority,
                      room_type_id
               FROM hospitalityclaw_rate_plan
               WHERE tenant_id = ? AND hotel_id = ? AND is_active = 1
                 AND start_date <= ? AND end_date >= ?
                 AND (room_type_id IS NULL OR room_type_id = ?)
               ORDER BY CASE WHEN room_type_id IS NOT NULL THEN 0 ELSE 1 END,
                        priority DESC, start_date DESC
               LIMIT 1""",
            (tenant_id, hotel_id, posting_date, posting_date, room_type_id),
        ).fetchone()
    except Exception:
        return {
            "date": posting_date,
            "rate": str(fallback),
            "source": "base",
            "rate_plan_id": None,
            "rate_plan_name": None,
            "rate_plan_code": None,
        }
    if not row:
        return {
            "date": posting_date,
            "rate": str(fallback),
            "source": "base",
            "rate_plan_id": None,
            "rate_plan_name": None,
            "rate_plan_code": None,
        }
    return {
        "date": posting_date,
        "rate": str(row["nightly_rate"]),
        "source": "rate_plan",
        "rate_plan_id": row["id"],
        "rate_plan_name": row["name"],
        "rate_plan_code": row["code"],
    }


def _resolve_nightly_rate(conn, tenant_id, hotel_id, room_type_id, posting_date, fallback):
    """Seasonal / dynamic rate plan wins over the reservation/base rate when active."""
    return _lookup_rate_for_night(
        conn, tenant_id, hotel_id, room_type_id, posting_date, fallback,
    )["rate"]


def _iter_nights(check_in: str, check_out: str):
    """Yield each night date from check-in inclusive to check-out exclusive."""
    start = date.fromisoformat(check_in)
    end = date.fromisoformat(check_out)
    cur = start
    while cur < end:
        yield cur.isoformat()
        cur += timedelta(days=1)


def _rate_window_priority(start_date: str, end_date: str, explicit=None) -> int:
    """Shorter windows outrank longer ones so Oct 1–10 beats all of October."""
    if explicit is not None and str(explicit).strip() != "":
        return int(explicit)
    days = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1
    # 1-day spikes → 999; full-year ≈ 635. Clamp so custom priorities can still win.
    return max(1, min(999, 1000 - days))


def _auto_rate_code(room_type_code: str | None, start_date: str, end_date: str) -> str:
    compact = "".join(ch for ch in (room_type_code or "ALL").upper() if ch.isalnum())[:8] or "ALL"
    return f"DYN-{compact}-{start_date.replace('-', '')}-{end_date.replace('-', '')}"


def _auto_rate_name(start_date: str, end_date: str, room_type_name: str | None = None) -> str:
    label = room_type_name or "All types"
    if start_date == end_date:
        return f"{label} {start_date}"
    return f"{label} {start_date} → {end_date}"


def _tax_rules_for(conn, tenant_id, hotel_id, charge_type: str):
    try:
        return conn.execute(
            """SELECT * FROM hospitalityclaw_tax_rule
               WHERE tenant_id = ? AND hotel_id = ? AND is_active = 1
                 AND (applies_to = 'all' OR applies_to = ?)
               ORDER BY is_default DESC, name""",
            (tenant_id, hotel_id, charge_type),
        ).fetchall()
    except Exception:
        return []


def _apply_tax_rules(conn, tenant_id, hotel_id, folio_id, base_amount, charge_type, posting_date,
                     channel_source=None):
    """Post tax lines for a taxable charge. Returns list of {tax_rule, amount, charge_id, gl}."""
    posted = []
    base = Decimal(str(base_amount))
    if base <= 0:
        return posted
    for rule in _tax_rules_for(conn, tenant_id, hotel_id, charge_type):
        pct = Decimal(str(rule["rate_percent"] or "0"))
        if pct <= 0:
            continue
        tax_amt = (base * pct / Decimal("100")).quantize(Decimal("0.01"))
        if tax_amt <= 0:
            continue
        desc = f"{rule['name']} ({pct}%)"
        charge_id, gl_ids = _post_folio_line(
            conn, tenant_id, folio_id, desc, str(tax_amt), "tax", posting_date,
            channel_source=channel_source,
        )
        posted.append({
            "tax_rule_id": rule["id"],
            "name": rule["name"],
            "rate_percent": str(pct),
            "amount": str(tax_amt),
            "charge_id": charge_id,
            "gl_posted": bool(gl_ids),
            "erp_tax_template_id": rule["erp_tax_template_id"],
        })
    return posted


def _bump_folio_balance(conn, tenant_id, folio_id, delta: Decimal):
    row = conn.execute(
        "SELECT balance FROM hospitalityclaw_folio WHERE id = ? AND tenant_id = ?",
        (folio_id, tenant_id),
    ).fetchone()
    new_bal = Decimal(str(row["balance"] or "0")) + delta
    conn.execute(
        "UPDATE hospitalityclaw_folio SET balance = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ? AND tenant_id = ?",
        (str(new_bal), folio_id, tenant_id),
    )


def _assert_min_length_of_stay(conn, tenant_id, hotel_id, room_type_id, check_in, check_out):
    """Refuse bookings that violate yield LOS or manual inventory controls."""
    try:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        api = os.path.join(root, "webclaw", "api")
        if api not in sys.path:
            sys.path.insert(0, api)
        from services.revenue import required_min_nights
        from services.inventory_controls import assert_stay_allowed
        required = required_min_nights(conn, tenant_id, hotel_id, room_type_id, check_in, check_out)
        assert_stay_allowed(conn, tenant_id, hotel_id, room_type_id, check_in, check_out)
    except ValueError as exc:
        err(str(exc), suggestion="Adjust dates or clear inventory restrictions.")
        return
    except Exception:
        required = 1
    try:
        nights = (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days
    except ValueError:
        return
    if nights < int(required or 1):
        err(
            f"Minimum stay is {required} night(s) for these dates "
            f"(this booking is {nights} night{'s' if nights != 1 else ''})",
            suggestion="Extend the stay, or pick dates without a length-of-stay restriction.",
        )


def _assert_property_accepting_bookings(conn, tenant_id: str):
    """Refuse new reservations when the owner closed the branch for booking."""
    from init_db import _table_columns

    try:
        cols = _table_columns(conn, "pms_tenant")
    except Exception:
        return
    if not cols or "id" not in cols:
        return
    if "accepting_bookings" not in cols:
        try:
            conn.execute(
                "ALTER TABLE pms_tenant ADD COLUMN accepting_bookings INTEGER NOT NULL DEFAULT 1"
            )
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return
    row = conn.execute(
        """SELECT status, COALESCE(accepting_bookings, 1) AS accepting_bookings
           FROM pms_tenant WHERE id = ? OR company_id = ? LIMIT 1""",
        (tenant_id, tenant_id),
    ).fetchone()
    if not row:
        return
    status = str(row["status"] or "active").lower()
    if status in ("suspended", "cancelled", "deleted"):
        err(
            "This property is not available for booking",
            suggestion="Choose another branch, or ask the owner to reopen it.",
        )
    accepting = row["accepting_bookings"]
    if accepting in (0, False, "0", "false", "False"):
        err(
            "This branch is closed for new bookings",
            suggestion="The owner closed this location for website and OTA sales. Reopen it under Branches to accept reservations again.",
        )


def hospitality_add_reservation(conn, args):
    init_db(conn)
    tenant_id = _tenant(args)
    if not args.hotel_id or not args.guest_id or not args.room_type_id:
        err("--hotel-id, --guest-id and --room-type-id are required")
    if not args.check_in or not args.check_out:
        err("--check-in and --check-out are required")
    if args.check_out <= args.check_in:
        err("check-out must be after check-in")
    _assert_property_accepting_bookings(conn, tenant_id)
    owned(conn, "hospitalityclaw_hotel", args.hotel_id, tenant_id, "Hotel")
    owned(conn, "hospitalityclaw_guest", args.guest_id, tenant_id, "Guest")
    rt = owned(conn, "hospitalityclaw_room_type", args.room_type_id, tenant_id, "Room type")
    _assert_min_length_of_stay(
        conn, tenant_id, args.hotel_id, args.room_type_id, args.check_in, args.check_out,
    )
    immediate = args.check_in <= _today_iso()
    _assert_room_type_capacity(
        conn, tenant_id, args.room_type_id, args.check_in, args.check_out,
        immediate=immediate,
        preferred_room_id=args.room_id or None,
        hotel_id=args.hotel_id,
    )
    rid = str(uuid.uuid4())
    if args.rate is not None and str(args.rate).strip() != "":
        rate = _money(args.rate, "rate")
    else:
        # Quote the check-in night from the dynamic calendar; each subsequent
        # night still resolves independently when room tariffs post.
        rate = _money(
            _resolve_nightly_rate(
                conn, tenant_id, args.hotel_id, args.room_type_id,
                args.check_in, rt["base_rate"] or "0",
            ),
            "rate",
        )
    import secrets
    booking_token = secrets.token_urlsafe(24)
    # Ensure column exists (idempotent migrate)
    from init_db import _table_columns

    res_cols = _table_columns(conn, "hospitalityclaw_reservation")
    if "booking_token" in res_cols:
        conn.execute(
            """INSERT INTO hospitalityclaw_reservation
               (id, tenant_id, hotel_id, guest_id, room_id, room_type_id,
                check_in, check_out, adults, children, status, rate, notes, booking_token)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?)""",
            (
                rid, tenant_id, args.hotel_id, args.guest_id, args.room_id,
                args.room_type_id, args.check_in, args.check_out,
                int(args.adults or 1), int(args.children or 0), rate, args.notes,
                booking_token,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO hospitalityclaw_reservation
               (id, tenant_id, hotel_id, guest_id, room_id, room_type_id,
                check_in, check_out, adults, children, status, rate, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?)""",
            (
                rid, tenant_id, args.hotel_id, args.guest_id, args.room_id,
                args.room_type_id, args.check_in, args.check_out,
                int(args.adults or 1), int(args.children or 0), rate, args.notes,
            ),
        )
        booking_token = None
    folio_id = _open_folio(conn, tenant_id, rid, args.guest_id)
    conn.commit()
    payload = {"reservation_id": rid, "folio_id": folio_id, "rate": rate}
    if booking_token:
        payload["booking_token"] = booking_token
        payload["guest_portal_path"] = f"/guest/{booking_token}"
    ok(payload)


def hospitality_list_reservations(conn, args):
    tenant_id = _tenant(args)
    sql = """SELECT r.*,
                    g.first_name, g.last_name, g.email AS guest_email,
                    rt.name AS room_type_name, rm.room_number,
                    f.id AS folio_id, f.balance AS folio_balance, f.status AS folio_status
             FROM hospitalityclaw_reservation r
             JOIN hospitalityclaw_guest g ON g.id = r.guest_id
             JOIN hospitalityclaw_room_type rt ON rt.id = r.room_type_id
             LEFT JOIN hospitalityclaw_room rm ON rm.id = r.room_id
             LEFT JOIN hospitalityclaw_folio f ON f.reservation_id = r.id AND f.tenant_id = r.tenant_id
             WHERE r.tenant_id = ?"""
    params = [tenant_id]
    if args.status:
        sql += " AND r.status = ?"
        params.append(args.status)
    from_date = getattr(args, "from_date", None)
    to_date = getattr(args, "to_date", None)
    if from_date:
        sql += " AND r.check_out > ?"
        params.append(from_date)
    if to_date:
        sql += " AND r.check_in < ?"
        params.append(to_date)
    _ok_paged(conn, "reservations", sql, params, args, "r.check_in DESC")


def hospitality_get_reservation(conn, args):
    tenant_id = _tenant(args)
    rid = args.id or args.reservation_id
    if not rid:
        err("--id is required")
    owned(conn, "hospitalityclaw_reservation", rid, tenant_id, "Reservation")
    row = conn.execute(
        """SELECT r.*,
                  g.first_name, g.last_name, g.email AS guest_email, g.phone AS guest_phone,
                  rt.name AS room_type_name, rm.room_number,
                  f.id AS folio_id, f.balance AS folio_balance, f.status AS folio_status
           FROM hospitalityclaw_reservation r
           JOIN hospitalityclaw_guest g ON g.id = r.guest_id
           JOIN hospitalityclaw_room_type rt ON rt.id = r.room_type_id
           LEFT JOIN hospitalityclaw_room rm ON rm.id = r.room_id
           LEFT JOIN hospitalityclaw_folio f ON f.reservation_id = r.id AND f.tenant_id = r.tenant_id
           WHERE r.tenant_id = ? AND r.id = ?""",
        (tenant_id, rid),
    ).fetchone()
    ok({"reservation": row_to_dict(row)})


def hospitality_assign_room(conn, args):
    """Assign (or reassign) a room to a reserved stay before check-in."""
    tenant_id = _tenant(args)
    rid = args.id or args.reservation_id
    room_id = args.room_id
    if not rid:
        err("--id is required")
    if not room_id:
        err("--room-id is required")
    res = owned(conn, "hospitalityclaw_reservation", rid, tenant_id, "Reservation")
    if res["status"] not in ("reserved", "pre_checked_in"):
        err(f"Can only assign a room to a reserved stay (status is '{res['status']}')")
    room = owned(conn, "hospitalityclaw_room", room_id, tenant_id, "Room")
    if room["room_type_id"] != res["room_type_id"]:
        err("Room type does not match this reservation")
    _assert_room_assignable(
        conn, tenant_id, room, res["check_in"], res["check_out"],
        immediate=res["check_in"] <= _today_iso(),
        exclude_reservation_id=rid,
    )
    conn.execute(
        "UPDATE hospitalityclaw_reservation SET room_id = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ? AND tenant_id = ?",
        (room_id, rid, tenant_id),
    )
    conn.commit()
    ok({
        "reservation_id": rid,
        "room_id": room_id,
        "room_number": room["room_number"],
    })


def hospitality_check_in(conn, args):
    tenant_id = _tenant(args)
    rid = args.id or args.reservation_id
    if not rid:
        err("--id is required")
    res = owned(conn, "hospitalityclaw_reservation", rid, tenant_id, "Reservation")
    if res["status"] not in ("reserved", "pre_checked_in"):
        err(f"Cannot check in a reservation in status '{res['status']}'")
    room_id = args.room_id or res["room_id"]
    if not room_id:
        err("--room-id is required at check-in")
    room = owned(conn, "hospitalityclaw_room", room_id, tenant_id, "Room")
    if room["room_type_id"] != res["room_type_id"]:
        err("Room type does not match this reservation")
    # Check-in is always on-the-spot: only a vacant clean room, no calendar clash.
    _assert_room_assignable(
        conn, tenant_id, room, res["check_in"], res["check_out"],
        immediate=True,
        exclude_reservation_id=rid,
    )
    conn.execute(
        "UPDATE hospitalityclaw_reservation SET status = 'checked_in', room_id = ?, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND tenant_id = ?",
        (room_id, rid, tenant_id),
    )
    conn.execute(
        "UPDATE hospitalityclaw_room SET status = 'occupied', updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ? AND tenant_id = ?",
        (room_id, tenant_id),
    )
    folio_id = _open_folio(conn, tenant_id, rid, res["guest_id"])
    today = date.today().isoformat()
    # OTA imports post the stay's room tariff up front. Do not post that night again.
    existing_room = conn.execute(
        """SELECT id, amount FROM hospitalityclaw_folio_charge
           WHERE tenant_id = ? AND folio_id = ? AND charge_type = 'room' AND posting_date = ?""",
        (tenant_id, folio_id, today),
    ).fetchone()
    if existing_room:
        charge_id, gl_ids, taxes = existing_room["id"], None, []
        amount = str(existing_room["amount"] or "0")
    else:
        rate = _resolve_nightly_rate(
            conn, tenant_id, res["hotel_id"], res["room_type_id"], today, res["rate"] or "0",
        )
        amount = str(Decimal(str(rate or "0")))
        charge_id, gl_ids = _post_folio_line(
            conn, tenant_id, folio_id, "Room charge", amount, "room", today,
        )
        taxes = _apply_tax_rules(
            conn, tenant_id, res["hotel_id"], folio_id, amount, "room", today,
        )
    conn.commit()
    ok({
        "reservation_id": rid,
        "room_id": room_id,
        "folio_id": folio_id,
        "room_charge": amount,
        "charge_id": charge_id,
        "gl_posted": bool(gl_ids),
        "taxes": taxes,
    })


def hospitality_check_out(conn, args):
    tenant_id = _tenant(args)
    rid = args.id or args.reservation_id
    if not rid:
        err("--id is required")
    res = owned(conn, "hospitalityclaw_reservation", rid, tenant_id, "Reservation")
    if res["status"] != "checked_in":
        err(f"Cannot check out a reservation in status '{res['status']}'")
    folio = conn.execute(
        "SELECT * FROM hospitalityclaw_folio WHERE tenant_id = ? AND reservation_id = ?",
        (tenant_id, rid),
    ).fetchone()
    if folio and Decimal(str(folio["balance"] or "0")) != 0:
        err(
            f"Folio still has a balance of {folio['balance']}. Post a payment first.",
            suggestion="Use hospitality-add-folio-charge with charge-type=payment.",
        )
    conn.execute(
        "UPDATE hospitalityclaw_reservation SET status = 'checked_out', "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND tenant_id = ?",
        (rid, tenant_id),
    )
    available_from = None
    if res["room_id"]:
        # After checkout: dirty → HK cleans → sellable after turnaround window.
        turnaround = _hk_turnaround_days(conn, tenant_id, res.get("hotel_id"))
        available_from = _add_days_iso(_today_iso(), turnaround)
        conn.execute(
            """UPDATE hospitalityclaw_room
               SET status = 'dirty', available_from = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND tenant_id = ?""",
            (available_from, res["room_id"], tenant_id),
        )
    if folio:
        conn.execute(
            "UPDATE hospitalityclaw_folio SET status = 'closed', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND tenant_id = ?",
            (folio["id"], tenant_id),
        )
    conn.commit()
    ok({
        "reservation_id": rid,
        "reservation_status": "checked_out",
        "room_id": res["room_id"],
        "room_status": "dirty" if res["room_id"] else None,
        "available_from": available_from,
        "housekeeping": "queued" if res["room_id"] else None,
    })


def hospitality_cancel_reservation(conn, args):
    """Cancel a reserved stay before arrival / check-in."""
    init_db(conn)
    tenant_id = _tenant(args)
    rid = args.id or args.reservation_id
    if not rid:
        err("--id is required")
    res = owned(conn, "hospitalityclaw_reservation", rid, tenant_id, "Reservation")
    if res["status"] not in ("reserved", "pre_checked_in"):
        err(
            f"Can only cancel a reserved stay (status is '{res['status']}')",
            suggestion="Checked-in guests must be checked out instead.",
        )
    folio = conn.execute(
        "SELECT * FROM hospitalityclaw_folio WHERE tenant_id = ? AND reservation_id = ?",
        (tenant_id, rid),
    ).fetchone()
    if folio and Decimal(str(folio["balance"] or "0")) != 0:
        err(
            f"Folio still has a balance of {folio['balance']}. Settle or reverse charges first.",
            suggestion="Use Billing to settle the folio, then cancel.",
        )

    reason = (getattr(args, "notes", None) or "").strip()
    notes = res["notes"] or ""
    if reason:
        stamp = f"[Cancelled] {reason}"
        notes = f"{notes}\n{stamp}".strip() if notes else stamp

    # Release any pre-assigned room (never occupied for reserved-only stays).
    conn.execute(
        """UPDATE hospitalityclaw_reservation
           SET status = 'cancelled', room_id = NULL, notes = ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ? AND tenant_id = ?""",
        (notes or None, rid, tenant_id),
    )
    if folio:
        conn.execute(
            "UPDATE hospitalityclaw_folio SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND tenant_id = ?",
            (folio["id"], tenant_id),
        )
    conn.commit()
    ok({
        "reservation_id": rid,
        "reservation_status": "cancelled",
        "folio_status": "cancelled" if folio else None,
        "room_released": bool(res["room_id"]),
    })


def hospitality_add_folio_charge(conn, args):
    tenant_id = _tenant(args)
    folio_id = args.folio_id
    if not folio_id or not args.description:
        err("--folio-id and --description are required")
    folio = owned(conn, "hospitalityclaw_folio", folio_id, tenant_id, "Folio")
    if folio["status"] != "open":
        err("Cannot post to a closed folio")
    charge_type = args.charge_type or "other"
    today = args.posting_date or date.today().isoformat()
    charge_id, gl_ids = _post_folio_line(
        conn, tenant_id, folio_id, args.description, args.amount, charge_type, today,
        payment_method=args.payment_method, reference=args.reference,
    )
    taxes = []
    if charge_type in ("room", "fnb"):
        hotel_id = _hotel_id_for_folio(conn, tenant_id, folio_id)
        if hotel_id:
            taxes = _apply_tax_rules(
                conn, tenant_id, hotel_id, folio_id, args.amount, charge_type, today,
            )
    conn.commit()
    ok({
        "charge_id": charge_id,
        "folio_id": folio_id,
        "gl_posted": bool(gl_ids),
        "taxes": taxes,
    })


def hospitality_list_folio_charges(conn, args):
    tenant_id = _tenant(args)
    sql = "SELECT * FROM hospitalityclaw_folio_charge WHERE tenant_id = ?"
    params = [tenant_id]
    if args.folio_id:
        sql += " AND folio_id = ?"
        params.append(args.folio_id)
    _ok_paged(conn, "charges", sql, params, args, "posting_date, created_at")


def hospitality_list_ledger_entries(conn, args):
    tenant_id = _tenant(args)
    sql = "SELECT * FROM hospitalityclaw_ledger_entry WHERE tenant_id = ?"
    params = [tenant_id]
    if args.folio_id:
        sql += " AND folio_id = ?"
        params.append(args.folio_id)
    _ok_paged(conn, "entries", sql, params, args, "posting_date, created_at")


def hospitality_list_transactions(conn, args):
    tenant_id = _tenant(args)
    sql = """SELECT t.*,
                    f.reservation_id,
                    g.first_name, g.last_name, g.email AS guest_email,
                    rm.room_number
             FROM hospitalityclaw_transaction t
             LEFT JOIN hospitalityclaw_folio f ON f.id = t.folio_id AND f.tenant_id = t.tenant_id
             LEFT JOIN hospitalityclaw_guest g ON g.id = f.guest_id
             LEFT JOIN hospitalityclaw_reservation r ON r.id = f.reservation_id
             LEFT JOIN hospitalityclaw_room rm ON rm.id = r.room_id
             WHERE t.tenant_id = ?"""
    params = [tenant_id]
    if args.folio_id:
        sql += " AND t.folio_id = ?"
        params.append(args.folio_id)
    from_date = getattr(args, "from_date", None)
    to_date = getattr(args, "to_date", None)
    if from_date:
        sql += " AND COALESCE(t.posted_at, t.created_at) >= ?"
        params.append(from_date)
    if to_date:
        # Inclusive end-of-day when only a date (YYYY-MM-DD) is provided.
        end = to_date if "T" in str(to_date) or " " in str(to_date) else f"{to_date}T23:59:59"
        sql += " AND COALESCE(t.posted_at, t.created_at) <= ?"
        params.append(end)
    status = getattr(args, "status", None)
    if status:
        sql += " AND t.status = ?"
        params.append(status)
    _ok_paged(conn, "transactions", sql, params, args, "COALESCE(t.posted_at, t.created_at) DESC")


def hospitality_list_folios(conn, args):
    init_db(conn)
    tenant_id = _tenant(args)
    sql = """SELECT f.*,
                    g.first_name, g.last_name, g.email AS guest_email,
                    r.check_in, r.check_out, r.status AS reservation_status,
                    r.rate AS room_rate, rm.room_number
             FROM hospitalityclaw_folio f
             JOIN hospitalityclaw_guest g ON g.id = f.guest_id
             JOIN hospitalityclaw_reservation r ON r.id = f.reservation_id
             LEFT JOIN hospitalityclaw_room rm ON rm.id = r.room_id
             WHERE f.tenant_id = ?"""
    params = [tenant_id]
    if args.reservation_id:
        sql += " AND f.reservation_id = ?"
        params.append(args.reservation_id)
    if args.status:
        sql += " AND f.status = ?"
        params.append(args.status)
    _ok_paged(conn, "folios", sql, params, args, "f.created_at DESC")


def hospitality_get_folio(conn, args):
    tenant_id = _tenant(args)
    folio_id = args.id or args.folio_id
    if not folio_id:
        err("--id is required")
    owned(conn, "hospitalityclaw_folio", folio_id, tenant_id, "Folio")
    row = conn.execute(
        """SELECT f.*,
                  g.first_name, g.last_name, g.email AS guest_email,
                  r.check_in, r.check_out, r.status AS reservation_status,
                  r.rate AS room_rate, rm.room_number, r.hotel_id
           FROM hospitalityclaw_folio f
           JOIN hospitalityclaw_guest g ON g.id = f.guest_id
           JOIN hospitalityclaw_reservation r ON r.id = f.reservation_id
           LEFT JOIN hospitalityclaw_room rm ON rm.id = r.room_id
           WHERE f.tenant_id = ? AND f.id = ?""",
        (tenant_id, folio_id),
    ).fetchone()
    charges = conn.execute(
        """SELECT charge_type, SUM(CAST(amount AS REAL)) AS total
           FROM hospitalityclaw_folio_charge
           WHERE tenant_id = ? AND folio_id = ?
           GROUP BY charge_type""",
        (tenant_id, folio_id),
    ).fetchall()
    breakdown = {c["charge_type"]: str(c["total"] or 0) for c in charges}
    ok({"folio": row_to_dict(row), "breakdown": breakdown})


def hospitality_settle_folio(conn, args):
    """Post a payment for the remaining folio balance so the stay can check out.

    Creates the hospitality payment line and double-entry GL via erpclaw-engine
    (Debit Cash / Credit AR) when company defaults are configured.
    """
    tenant_id = _tenant(args)
    folio_id = args.id or args.folio_id
    if not folio_id:
        err("--folio-id is required")
    folio = owned(conn, "hospitalityclaw_folio", folio_id, tenant_id, "Folio")
    if folio["status"] != "open":
        err("Cannot settle a closed folio")
    balance = Decimal(str(folio["balance"] or "0"))
    if balance < 0:
        err("Folio has a credit balance; post an adjustment first")
    if balance == 0:
        ok({"folio_id": folio_id, "settled": True, "amount": "0", "already_settled": True})
        return
    today = args.posting_date or date.today().isoformat()
    charge_id, gl_ids = _post_folio_line(
        conn, tenant_id, folio_id,
        args.description or "Settlement",
        str(balance), "payment", today,
        payment_method=args.payment_method or "cash",
        reference=args.reference,
    )
    conn.commit()
    ok({
        "folio_id": folio_id,
        "settled": True,
        "amount": str(balance),
        "charge_id": charge_id,
        "gl_posted": bool(gl_ids),
        "payment_method": args.payment_method or "cash",
    })


def _post_one_room_tariff(conn, tenant_id, folio, reservation, posting_date):
    folio = dict(folio)
    reservation = dict(reservation)
    existing = conn.execute(
        """SELECT id FROM hospitalityclaw_folio_charge
           WHERE tenant_id = ? AND folio_id = ? AND charge_type = 'room' AND posting_date = ?""",
        (tenant_id, folio["id"], posting_date),
    ).fetchone()
    if existing:
        return existing["id"], True, None, []
    hotel_id = reservation.get("hotel_id") or folio.get("hotel_id")
    room_type_id = reservation.get("room_type_id")
    rate = reservation.get("rate") or "0"
    if hotel_id and room_type_id:
        rate = _resolve_nightly_rate(
            conn, tenant_id, hotel_id, room_type_id, posting_date, rate,
        )
    amount = str(Decimal(str(rate or "0")))
    charge_id, gl_ids = _post_folio_line(
        conn, tenant_id, folio["id"],
        f"Room tariff {posting_date}",
        amount, "room", posting_date,
    )
    taxes = []
    if hotel_id:
        taxes = _apply_tax_rules(
            conn, tenant_id, hotel_id, folio["id"], amount, "room", posting_date,
        )
    return charge_id, False, gl_ids, taxes


def hospitality_post_room_tariff(conn, args):
    """Auto-post the nightly room rate to the guest folio and general ledger."""
    tenant_id = _tenant(args)
    today = args.posting_date or date.today().isoformat()
    folio_id = args.folio_id or args.id
    reservation_id = args.reservation_id

    targets = []
    if folio_id:
        folio = owned(conn, "hospitalityclaw_folio", folio_id, tenant_id, "Folio")
        res = owned(conn, "hospitalityclaw_reservation", folio["reservation_id"], tenant_id, "Reservation")
        targets.append((folio, res))
    elif reservation_id:
        res = owned(conn, "hospitalityclaw_reservation", reservation_id, tenant_id, "Reservation")
        folio_id = _open_folio(conn, tenant_id, res["id"], res["guest_id"])
        folio = owned(conn, "hospitalityclaw_folio", folio_id, tenant_id, "Folio")
        targets.append((folio, res))
    else:
        rows = conn.execute(
            """SELECT f.*, r.rate, r.id AS reservation_pk, r.status AS res_status,
                      r.guest_id AS res_guest, r.hotel_id AS res_hotel,
                      r.room_type_id AS res_room_type
               FROM hospitalityclaw_folio f
               JOIN hospitalityclaw_reservation r ON r.id = f.reservation_id
               WHERE f.tenant_id = ? AND f.status = 'open' AND r.status = 'checked_in'""",
            (tenant_id,),
        ).fetchall()
        for row in rows:
            folio = dict(row)
            res = {
                "id": row["reservation_pk"],
                "rate": row["rate"],
                "status": row["res_status"],
                "guest_id": row["res_guest"],
                "hotel_id": row["res_hotel"],
                "room_type_id": row["res_room_type"],
            }
            targets.append((folio, res))

    posted = []
    skipped = []
    gl_count = 0
    tax_count = 0
    for folio, res in targets:
        if folio["status"] != "open":
            skipped.append({"folio_id": folio["id"], "reason": "closed"})
            continue
        if res["status"] != "checked_in":
            skipped.append({"folio_id": folio["id"], "reason": f"reservation is {res['status']}"})
            continue
        charge_id, already, gl_ids, taxes = _post_one_room_tariff(
            conn, tenant_id, folio, res, today,
        )
        if already:
            skipped.append({"folio_id": folio["id"], "charge_id": charge_id, "already_posted": True})
        else:
            posted.append({
                "folio_id": folio["id"],
                "charge_id": charge_id,
                "gl_posted": bool(gl_ids),
                "taxes": taxes,
            })
            if gl_ids:
                gl_count += 1
            tax_count += len(taxes)
    conn.commit()
    ok({
        "posting_date": today,
        "posted": posted,
        "skipped": skipped,
        "posted_count": len(posted),
        "gl_posted_count": gl_count,
        "tax_posted_count": tax_count,
    })


def _boolish(value, default=True) -> int:
    if value is None:
        return 1 if default else 0
    if isinstance(value, bool):
        return 1 if value else 0
    text = str(value).strip().lower()
    if text in ("0", "false", "no", "off"):
        return 0
    if text in ("1", "true", "yes", "on"):
        return 1
    return 1 if default else 0


def hospitality_add_rate_plan(conn, args):
    tenant_id = _tenant(args)
    if not args.hotel_id or not args.name or not args.code:
        err("--hotel-id, --name and --code are required")
    if not args.start_date or not args.end_date:
        err("--start-date and --end-date are required")
    if args.end_date < args.start_date:
        err("end-date must be on or after start-date")
    owned(conn, "hospitalityclaw_hotel", args.hotel_id, tenant_id, "Hotel")
    if args.room_type_id:
        owned(conn, "hospitalityclaw_room_type", args.room_type_id, tenant_id, "Room type")
    rid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_rate_plan
           (id, tenant_id, hotel_id, room_type_id, name, code, start_date, end_date,
            nightly_rate, priority, is_active, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            rid, tenant_id, args.hotel_id, args.room_type_id or None,
            args.name.strip(), args.code.strip().upper(),
            args.start_date, args.end_date,
            _money(args.nightly_rate or args.base_rate or "0", "nightly_rate"),
            int(args.priority or 0),
            _boolish(getattr(args, "is_active", None), True),
            args.notes,
        ),
    )
    conn.commit()
    ok({"rate_plan_id": rid})


def hospitality_set_rate_window(conn, args):
    """Create or update a dynamic rate window for a month or date range.

    Designed for advance calendar pricing: set October at $100, then override
    Oct 1–10 at $150. Shorter windows automatically get higher priority so they
    win over month-long (or year-long) rates. Night audit / check-in pick the
    matching window for each posting date automatically.
    """
    tenant_id = _tenant(args)
    if not args.hotel_id:
        err("--hotel-id is required")
    if not args.start_date or not args.end_date:
        err("--start-date and --end-date are required")
    if args.end_date < args.start_date:
        err("end-date must be on or after start-date")
    if args.nightly_rate is None and args.base_rate is None and args.rate is None:
        err("--nightly-rate is required")
    owned(conn, "hospitalityclaw_hotel", args.hotel_id, tenant_id, "Hotel")
    room_type_id = args.room_type_id or None
    rt_name = None
    rt_code = None
    if room_type_id:
        rt = owned(conn, "hospitalityclaw_room_type", room_type_id, tenant_id, "Room type")
        rt_name = rt["name"]
        rt_code = rt["code"]
    nightly = _money(
        args.nightly_rate if args.nightly_rate is not None
        else (args.rate if args.rate is not None else args.base_rate),
        "nightly_rate",
    )
    priority = _rate_window_priority(args.start_date, args.end_date, args.priority)
    name = (args.name or "").strip() or _auto_rate_name(args.start_date, args.end_date, rt_name)
    code = (args.code or "").strip().upper() or _auto_rate_code(rt_code, args.start_date, args.end_date)
    notes = args.notes
    is_active = _boolish(getattr(args, "is_active", None), True)

    existing = None
    if args.id:
        existing = owned(conn, "hospitalityclaw_rate_plan", args.id, tenant_id, "Rate plan")
    else:
        # Prefer exact window match for the same room type (or hotel-wide NULL).
        if room_type_id:
            existing = conn.execute(
                """SELECT * FROM hospitalityclaw_rate_plan
                   WHERE tenant_id = ? AND hotel_id = ? AND room_type_id = ?
                     AND start_date = ? AND end_date = ?""",
                (tenant_id, args.hotel_id, room_type_id, args.start_date, args.end_date),
            ).fetchone()
        else:
            existing = conn.execute(
                """SELECT * FROM hospitalityclaw_rate_plan
                   WHERE tenant_id = ? AND hotel_id = ? AND room_type_id IS NULL
                     AND start_date = ? AND end_date = ?""",
                (tenant_id, args.hotel_id, args.start_date, args.end_date),
            ).fetchone()
        if not existing:
            existing = conn.execute(
                """SELECT * FROM hospitalityclaw_rate_plan
                   WHERE tenant_id = ? AND hotel_id = ? AND code = ?""",
                (tenant_id, args.hotel_id, code),
            ).fetchone()

    if existing:
        rid = existing["id"]
        conn.execute(
            """UPDATE hospitalityclaw_rate_plan
               SET room_type_id = ?, name = ?, code = ?, start_date = ?, end_date = ?,
                   nightly_rate = ?, priority = ?, is_active = ?, notes = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND tenant_id = ?""",
            (
                room_type_id, name, code, args.start_date, args.end_date,
                nightly, priority, is_active, notes, rid, tenant_id,
            ),
        )
        conn.commit()
        ok({
            "rate_plan_id": rid,
            "updated": True,
            "name": name,
            "code": code,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "nightly_rate": nightly,
            "priority": priority,
        })
        return

    rid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_rate_plan
           (id, tenant_id, hotel_id, room_type_id, name, code, start_date, end_date,
            nightly_rate, priority, is_active, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            rid, tenant_id, args.hotel_id, room_type_id, name, code,
            args.start_date, args.end_date, nightly, priority, is_active, notes,
        ),
    )
    conn.commit()
    ok({
        "rate_plan_id": rid,
        "updated": False,
        "name": name,
        "code": code,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "nightly_rate": nightly,
        "priority": priority,
    })


def hospitality_rate_calendar(conn, args):
    """Resolve the effective nightly rate for each night in a date window.

    Hotel admins use this to preview the calendar; booking flows can quote a
    stay. When --check-in/--check-out are provided they define the stay nights
    (checkout exclusive). Otherwise --start-date/--end-date are inclusive.
    """
    tenant_id = _tenant(args)
    if not args.hotel_id or not args.room_type_id:
        err("--hotel-id and --room-type-id are required")
    owned(conn, "hospitalityclaw_hotel", args.hotel_id, tenant_id, "Hotel")
    rt = owned(conn, "hospitalityclaw_room_type", args.room_type_id, tenant_id, "Room type")
    base = _money(rt["base_rate"] or "0", "base_rate")

    if args.check_in and args.check_out:
        if args.check_out <= args.check_in:
            err("check-out must be after check-in")
        nights = list(_iter_nights(args.check_in, args.check_out))
        range_start, range_end = args.check_in, args.check_out
    else:
        start = args.start_date or args.from_date
        end = args.end_date or args.to_date
        if not start or not end:
            err("--start-date and --end-date (or --check-in/--check-out) are required")
        if end < start:
            err("end-date must be on or after start-date")
        # Inclusive calendar range for admin preview.
        nights = list(_iter_nights(start, (date.fromisoformat(end) + timedelta(days=1)).isoformat()))
        range_start, range_end = start, end

    if len(nights) > 366:
        err("Date range cannot exceed 366 nights")

    resolved = [
        _lookup_rate_for_night(
            conn, tenant_id, args.hotel_id, args.room_type_id, night, base,
        )
        for night in nights
    ]
    total = sum(Decimal(n["rate"]) for n in resolved)
    ok({
        "hotel_id": args.hotel_id,
        "room_type_id": args.room_type_id,
        "room_type_name": rt["name"],
        "room_type_code": rt["code"],
        "base_rate": base,
        "start_date": range_start,
        "end_date": range_end,
        "nights": len(resolved),
        "nightly_rates": resolved,
        "total": str(total.quantize(Decimal("0.01"))),
        "average_nightly": str(
            (total / len(resolved)).quantize(Decimal("0.01")) if resolved else Decimal("0")
        ),
    })


def hospitality_list_rate_plans(conn, args):
    tenant_id = _tenant(args)
    sql = """SELECT rp.*, rt.name AS room_type_name, rt.code AS room_type_code
             FROM hospitalityclaw_rate_plan rp
             LEFT JOIN hospitalityclaw_room_type rt ON rt.id = rp.room_type_id
             WHERE rp.tenant_id = ?"""
    params = [tenant_id]
    if args.hotel_id:
        sql += " AND rp.hotel_id = ?"
        params.append(args.hotel_id)
    if args.room_type_id:
        sql += " AND rp.room_type_id = ?"
        params.append(args.room_type_id)
    _ok_paged(conn, "rate_plans", sql, params, args, "rp.start_date DESC, rp.priority DESC")


def hospitality_update_rate_plan(conn, args):
    tenant_id = _tenant(args)
    rid = args.id
    if not rid:
        err("--id is required")
    owned(conn, "hospitalityclaw_rate_plan", rid, tenant_id, "Rate plan")
    fields = []
    params = []
    mapping = {
        "name": args.name,
        "code": args.code.strip().upper() if args.code else None,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "nightly_rate": _money(args.nightly_rate, "nightly_rate") if args.nightly_rate is not None else None,
        "priority": int(args.priority) if args.priority is not None else None,
        "notes": args.notes,
    }
    if getattr(args, "is_active", None) is not None:
        mapping["is_active"] = _boolish(args.is_active, True)
    for col, val in mapping.items():
        if val is None:
            continue
        fields.append(f"{col} = ?")
        params.append(val)
    if args.room_type_id is not None:
        if args.room_type_id == "":
            fields.append("room_type_id = NULL")
        else:
            owned(conn, "hospitalityclaw_room_type", args.room_type_id, tenant_id, "Room type")
            fields.append("room_type_id = ?")
            params.append(args.room_type_id)
    if not fields:
        err("No fields to update")
    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.extend([rid, tenant_id])
    conn.execute(
        f"UPDATE hospitalityclaw_rate_plan SET {', '.join(fields)} WHERE id = ? AND tenant_id = ?",
        params,
    )
    conn.commit()
    ok({
        "rate_plan_id": rid,
        "updated_fields": [f.split(" =")[0] for f in fields if "updated_at" not in f],
    })


def hospitality_delete_rate_plan(conn, args):
    tenant_id = _tenant(args)
    rid = args.id
    if not rid:
        err("--id is required")
    owned(conn, "hospitalityclaw_rate_plan", rid, tenant_id, "Rate plan")
    conn.execute(
        "DELETE FROM hospitalityclaw_rate_plan WHERE id = ? AND tenant_id = ?",
        (rid, tenant_id),
    )
    conn.commit()
    ok({"deleted": True, "rate_plan_id": rid})


def hospitality_add_tax_rule(conn, args):
    tenant_id = _tenant(args)
    if not args.hotel_id or not args.name:
        err("--hotel-id and --name are required")
    owned(conn, "hospitalityclaw_hotel", args.hotel_id, tenant_id, "Hotel")
    applies = (args.applies_to or "room").strip().lower()
    if applies not in ("room", "fnb", "all"):
        err("applies-to must be room, fnb, or all")
    rid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO hospitalityclaw_tax_rule
           (id, tenant_id, hotel_id, name, rate_percent, applies_to,
            erp_tax_template_id, is_default, is_active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            rid, tenant_id, args.hotel_id, args.name.strip(),
            _money(args.rate_percent or args.rate or "0", "rate_percent"),
            applies,
            getattr(args, "tax_template_id", None) or getattr(args, "erp_tax_template_id", None),
            _boolish(getattr(args, "is_default", None), False),
            _boolish(getattr(args, "is_active", None), True),
        ),
    )
    conn.commit()
    ok({"tax_rule_id": rid})


def hospitality_list_tax_rules(conn, args):
    tenant_id = _tenant(args)
    sql = "SELECT * FROM hospitalityclaw_tax_rule WHERE tenant_id = ?"
    params = [tenant_id]
    if args.hotel_id:
        sql += " AND hotel_id = ?"
        params.append(args.hotel_id)
    _ok_paged(conn, "tax_rules", sql, params, args, "is_default DESC, name")


def hospitality_update_tax_rule(conn, args):
    tenant_id = _tenant(args)
    rid = args.id
    if not rid:
        err("--id is required")
    owned(conn, "hospitalityclaw_tax_rule", rid, tenant_id, "Tax rule")
    fields = []
    params = []
    if args.name:
        fields.append("name = ?")
        params.append(args.name.strip())
    rate = args.rate_percent if args.rate_percent is not None else args.rate
    if rate is not None:
        fields.append("rate_percent = ?")
        params.append(_money(rate, "rate_percent"))
    if args.applies_to:
        applies = args.applies_to.strip().lower()
        if applies not in ("room", "fnb", "all"):
            err("applies-to must be room, fnb, or all")
        fields.append("applies_to = ?")
        params.append(applies)
    tmpl = getattr(args, "tax_template_id", None)
    if tmpl is not None:
        fields.append("erp_tax_template_id = ?")
        params.append(tmpl or None)
    if getattr(args, "is_default", None) is not None:
        fields.append("is_default = ?")
        params.append(_boolish(args.is_default, False))
    if getattr(args, "is_active", None) is not None:
        fields.append("is_active = ?")
        params.append(_boolish(args.is_active, True))
    if not fields:
        err("No fields to update")
    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.extend([rid, tenant_id])
    conn.execute(
        f"UPDATE hospitalityclaw_tax_rule SET {', '.join(fields)} WHERE id = ? AND tenant_id = ?",
        params,
    )
    conn.commit()
    ok({"tax_rule_id": rid})


def hospitality_delete_tax_rule(conn, args):
    tenant_id = _tenant(args)
    rid = args.id
    if not rid:
        err("--id is required")
    owned(conn, "hospitalityclaw_tax_rule", rid, tenant_id, "Tax rule")
    conn.execute(
        "DELETE FROM hospitalityclaw_tax_rule WHERE id = ? AND tenant_id = ?",
        (rid, tenant_id),
    )
    conn.commit()
    ok({"deleted": True, "tax_rule_id": rid})


ACTIONS = {
    "hospitality-initialize-schema": hospitality_initialize_schema,
    "hospitality-seed-defaults": hospitality_seed_defaults,
    "hospitality-status": hospitality_status,
    "hospitality-list-hotels": hospitality_list_hotels,
    "hospitality-get-hotel": hospitality_get_hotel,
    "hospitality-update-hotel": hospitality_update_hotel,
    "hospitality-add-room-type": hospitality_add_room_type,
    "hospitality-list-room-types": hospitality_list_room_types,
    "hospitality-get-room-type": hospitality_get_room_type,
    "hospitality-update-room-type": hospitality_update_room_type,
    "hospitality-delete-room-type": hospitality_delete_room_type,
    "hospitality-add-room": hospitality_add_room,
    "hospitality-list-rooms": hospitality_list_rooms,
    "hospitality-get-room": hospitality_get_room,
    "hospitality-update-room": hospitality_update_room,
    "hospitality-delete-room": hospitality_delete_room,
    "hospitality-update-room-status": hospitality_update_room_status,
    "hospitality-add-guest": hospitality_add_guest,
    "hospitality-list-guests": hospitality_list_guests,
    "hospitality-get-guest": hospitality_get_guest,
    "hospitality-add-reservation": hospitality_add_reservation,
    "hospitality-list-reservations": hospitality_list_reservations,
    "hospitality-get-reservation": hospitality_get_reservation,
    "hospitality-assign-room": hospitality_assign_room,
    "hospitality-check-in": hospitality_check_in,
    "hospitality-check-out": hospitality_check_out,
    "hospitality-cancel-reservation": hospitality_cancel_reservation,
    "hospitality-add-folio-charge": hospitality_add_folio_charge,
    "hospitality-list-folio-charges": hospitality_list_folio_charges,
    "hospitality-list-ledger-entries": hospitality_list_ledger_entries,
    "hospitality-list-transactions": hospitality_list_transactions,
    "hospitality-list-folios": hospitality_list_folios,
    "hospitality-get-folio": hospitality_get_folio,
    "hospitality-list-revenue": hospitality_list_revenue,
    "hospitality-settle-folio": hospitality_settle_folio,
    "hospitality-post-room-tariff": hospitality_post_room_tariff,
    "hospitality-add-rate-plan": hospitality_add_rate_plan,
    "hospitality-list-rate-plans": hospitality_list_rate_plans,
    "hospitality-update-rate-plan": hospitality_update_rate_plan,
    "hospitality-delete-rate-plan": hospitality_delete_rate_plan,
    "hospitality-set-rate-window": hospitality_set_rate_window,
    "hospitality-rate-calendar": hospitality_rate_calendar,
    "hospitality-add-tax-rule": hospitality_add_tax_rule,
    "hospitality-list-tax-rules": hospitality_list_tax_rules,
    "hospitality-update-tax-rule": hospitality_update_tax_rule,
    "hospitality-delete-tax-rule": hospitality_delete_tax_rule,
}

# WebClaw UI routing expects list-/add-/get- names (e.g. /skills/hospitalityclaw/rooms).
for _alias, _target in {
    "status": "hospitality-status",
    "list-hotels": "hospitality-list-hotels",
    "get-hotel": "hospitality-get-hotel",
    "update-hotel": "hospitality-update-hotel",
    "add-room-type": "hospitality-add-room-type",
    "list-room-types": "hospitality-list-room-types",
    "get-room-type": "hospitality-get-room-type",
    "update-room-type": "hospitality-update-room-type",
    "delete-room-type": "hospitality-delete-room-type",
    "add-room": "hospitality-add-room",
    "list-rooms": "hospitality-list-rooms",
    "get-room": "hospitality-get-room",
    "update-room": "hospitality-update-room",
    "delete-room": "hospitality-delete-room",
    "update-room-status": "hospitality-update-room-status",
    "add-guest": "hospitality-add-guest",
    "list-guests": "hospitality-list-guests",
    "get-guest": "hospitality-get-guest",
    "add-reservation": "hospitality-add-reservation",
    "list-reservations": "hospitality-list-reservations",
    "get-reservation": "hospitality-get-reservation",
    "assign-room": "hospitality-assign-room",
    "check-in": "hospitality-check-in",
    "check-out": "hospitality-check-out",
    "cancel-reservation": "hospitality-cancel-reservation",
    "add-folio-charge": "hospitality-add-folio-charge",
    "list-folio-charges": "hospitality-list-folio-charges",
    "list-ledger-entries": "hospitality-list-ledger-entries",
    "list-transactions": "hospitality-list-transactions",
    "list-folios": "hospitality-list-folios",
    "get-folio": "hospitality-get-folio",
    "list-revenue": "hospitality-list-revenue",
    "settle-folio": "hospitality-settle-folio",
    "post-room-tariff": "hospitality-post-room-tariff",
    "add-rate-plan": "hospitality-add-rate-plan",
    "list-rate-plans": "hospitality-list-rate-plans",
    "update-rate-plan": "hospitality-update-rate-plan",
    "delete-rate-plan": "hospitality-delete-rate-plan",
    "set-rate-window": "hospitality-set-rate-window",
    "rate-calendar": "hospitality-rate-calendar",
    "add-tax-rule": "hospitality-add-tax-rule",
    "list-tax-rules": "hospitality-list-tax-rules",
    "update-tax-rule": "hospitality-update-tax-rule",
    "delete-tax-rule": "hospitality-delete-tax-rule",
}.items():
    ACTIONS[_alias] = ACTIONS[_target]


def main():
    parser = SafeArgumentParser(description="HospitalityClaw hotel PMS")
    parser.add_argument("--action", required=True, choices=sorted(ACTIONS.keys()))
    parser.add_argument("--company-id", default=None)
    parser.add_argument("--id", default=None)
    parser.add_argument("--hotel-id", default=None)
    parser.add_argument("--hotel-name", default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument("--code", default=None)
    parser.add_argument("--base-rate", default=None)
    parser.add_argument("--max-occupancy", default=None)
    parser.add_argument("--description", default=None)
    parser.add_argument("--room-type-id", default=None)
    parser.add_argument("--room-id", default=None)
    parser.add_argument("--room-number", default=None)
    parser.add_argument("--floor", default=None)
    parser.add_argument("--status", default=None)
    parser.add_argument("--available-from", default=None)
    parser.add_argument("--cleaning-date", default=None)
    parser.add_argument("--guest-id", default=None)
    parser.add_argument("--first-name", default=None)
    parser.add_argument("--last-name", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--phone", default=None)
    parser.add_argument("--nationality", default=None)
    parser.add_argument("--id-number", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument("--reservation-id", default=None)
    parser.add_argument("--check-in", default=None)
    parser.add_argument("--check-out", default=None)
    parser.add_argument("--adults", default=None)
    parser.add_argument("--children", default=None)
    parser.add_argument("--rate", default=None)
    parser.add_argument("--folio-id", default=None)
    parser.add_argument("--amount", default=None)
    parser.add_argument("--charge-type", default=None)
    parser.add_argument("--posting-date", default=None)
    parser.add_argument("--payment-method", default=None)
    parser.add_argument("--reference", default=None)
    parser.add_argument("--timezone", default=None)
    parser.add_argument("--currency", default=None)
    parser.add_argument("--country", default=None)
    parser.add_argument("--address", default=None)
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--nightly-rate", default=None)
    parser.add_argument("--priority", default=None)
    parser.add_argument("--is-active", default=None)
    parser.add_argument("--is-default", default=None)
    parser.add_argument("--applies-to", default=None)
    parser.add_argument("--rate-percent", default=None)
    parser.add_argument("--tax-template-id", default=None)
    parser.add_argument("--settings-json", default=None)
    parser.add_argument("--check-in-time", default=None)
    parser.add_argument("--check-out-time", default=None)
    parser.add_argument("--cancellation-policy", default=None)
    parser.add_argument("--payment-gateway", default=None)
    parser.add_argument("--payment-publishable-key", default=None)
    parser.add_argument("--payment-secret-key", default=None)
    parser.add_argument("--payment-webhook-secret", default=None)
    parser.add_argument("--limit", default=None)
    parser.add_argument("--offset", default=None)
    parser.add_argument("--search", default=None)
    args, unknown = parser.parse_known_args()
    check_unknown_args(parser, unknown)

    conn = get_connection()
    tenant_id = resolve_tenant_id(args.company_id, args.tenant_id, allow_missing=args.action == "hospitality-initialize-schema")
    if tenant_id:
        bind_connection(conn, tenant_id)
        args.tenant_id = tenant_id
    ACTIONS[args.action](conn, args)


if __name__ == "__main__":
    main()
