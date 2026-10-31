"""L1 unit tests: Wave 2 M5 — putaway + pick list + persisted hard reservation.

Covers (per the M5 test plan):
  - putaway rule precedence: item match beats item-group match, ties by priority ASC
  - pick-list end-to-end: SO → create → submit (reserve) → mark-picked → complete
  - reservation release on pick-list cancel frees the held qty
  - available-qty math with overlapping reservations (hard-block on over-issue)
  - manual reservation over-reserve is rejected
  - get-projected-qty fallback parity (SO-derived when no persisted rows)

Uses the shared inventory test env (build_inventory_env): full GL accounts, two
warehouses, item1 with 100 units of stock. Exact Decimal-as-text assertions.
"""
import sys
import uuid

import pytest

from inventory_helpers import (
    load_db_query, call_action, ns, is_ok, is_error,
    seed_item, seed_warehouse, seed_stock_entry_sle, seed_account, _uuid,
)

inv = load_db_query()

# Defaults for every flag an M5 action reads, so ns() needs only the few that matter.
_DEFAULTS = dict(
    id=None, name=None, priority=None, target_warehouse_id=None,
    match_item_id=None, match_item_group=None, active_only=False,
    stock_entry_id=None, sales_order_id=None, pick_list_id=None,
    item_id=None, warehouse_id=None, warehouse=None, qty=None, source_bin=None,
    picked_qty=None, voucher_type=None, voucher_id=None, reservation_status=None,
    item_status=None, company_id=None, company_name=None, reason=None,
    posting_date=None, entry_type=None, items=None, db_path=None,
)


def _ns(**kw):
    d = dict(_DEFAULTS)
    d.update(kw)
    return ns(**d)


def _seed_so(conn, env, item_id, qty="10"):
    """Insert a confirmed sales order with one open line for item_id at warehouse."""
    cust = _uuid()
    conn.execute("INSERT INTO customer (id, name, company_id) VALUES (?, 'Cust', ?)",
                 (cust, env["company_id"]))
    so = _uuid()
    conn.execute("INSERT INTO sales_order (id, customer_id, order_date, status, company_id) "
                 "VALUES (?, ?, '2026-06-19', 'confirmed', ?)",
                 (so, cust, env["company_id"]))
    conn.execute("INSERT INTO sales_order_item "
                 "(id, sales_order_id, item_id, quantity, delivered_qty, warehouse_id) "
                 "VALUES (?, ?, ?, ?, '0', ?)",
                 (_uuid(), so, item_id, qty, env["warehouse"]))
    conn.commit()
    return so


# ── Putaway precedence ──────────────────────────────────────────────────────

def test_putaway_item_match_beats_item_group(conn, env):
    """An item-level rule wins over an item-group rule even when the group rule
    has a lower (higher-precedence) priority number."""
    # item-group rule, priority 10 (high precedence within its class).
    r1 = call_action(inv.add_putaway_rule, conn, _ns(
        name="group-rule", match_item_group="Widgets",
        target_warehouse_id=env["warehouse2"], priority=10, company_id=env["company_id"]))
    assert is_ok(r1), r1
    # item rule, priority 50 (lower precedence number-wise, but item class wins).
    r2 = call_action(inv.add_putaway_rule, conn, _ns(
        name="item-rule", match_item_id=env["item1"],
        target_warehouse_id=env["warehouse"], priority=50, company_id=env["company_id"]))
    assert is_ok(r2), r2

    # _resolve_putaway_target must return the ITEM rule's target (warehouse), not
    # the group rule's (warehouse2), proving item class beats group class.
    target = inv._resolve_putaway_target(conn, env["company_id"], env["item1"], "Widgets")
    assert target == env["warehouse"]


def test_putaway_priority_tie_within_class(conn, env):
    """Within the item class, the lower priority number wins."""
    call_action(inv.add_putaway_rule, conn, _ns(
        name="lo-pri", match_item_id=env["item1"],
        target_warehouse_id=env["warehouse2"], priority=5, company_id=env["company_id"]))
    call_action(inv.add_putaway_rule, conn, _ns(
        name="hi-pri", match_item_id=env["item1"],
        target_warehouse_id=env["warehouse"], priority=99, company_id=env["company_id"]))
    target = inv._resolve_putaway_target(conn, env["company_id"], env["item1"], None)
    assert target == env["warehouse2"]  # priority 5 beats 99


def test_putaway_requires_a_match_clause(conn, env):
    """add-putaway-rule with neither match-item nor match-item-group is rejected."""
    res = call_action(inv.add_putaway_rule, conn, _ns(
        name="bad", target_warehouse_id=env["warehouse"], company_id=env["company_id"]))
    assert is_error(res)
    assert "match-item" in res["message"]


def test_delete_putaway_rule_is_soft(conn, env):
    """delete-putaway-rule deactivates (is_active=0), it does not hard-delete."""
    r = call_action(inv.add_putaway_rule, conn, _ns(
        name="temp", match_item_id=env["item1"],
        target_warehouse_id=env["warehouse"], company_id=env["company_id"]))
    rid = r["putaway_rule_id"]
    d = call_action(inv.delete_putaway_rule, conn, _ns(id=rid))
    assert is_ok(d)
    row = conn.execute("SELECT is_active FROM putaway_rule WHERE id = ?", (rid,)).fetchone()
    assert row["is_active"] == 0
    # A deactivated rule no longer routes.
    assert inv._resolve_putaway_target(conn, env["company_id"], env["item1"], None) is None


# ── Pick-list end-to-end ────────────────────────────────────────────────────

def test_pick_list_end_to_end(conn, env):
    """SO → create-pick-list → submit (reserve) → mark-picked → complete (consume)."""
    so = _seed_so(conn, env, env["item1"], "10")

    pl = call_action(inv.create_pick_list, conn, _ns(sales_order_id=so))
    assert is_ok(pl), pl
    assert pl["line_count"] == 1
    assert pl["name"].startswith("PICK-")

    sub = call_action(inv.submit_pick_list, conn, _ns(id=pl["pick_list_id"]))
    assert is_ok(sub), sub
    assert sub["reservations_created"] == 1
    # An ACTIVE reservation of 10 now exists for (item1, warehouse).
    res = conn.execute(
        "SELECT reserved_qty, status FROM stock_reservation_entry "
        "WHERE voucher_type = 'pick_list' AND voucher_id = ?",
        (pl["pick_list_id"],),
    ).fetchone()
    assert res["reserved_qty"] == "10.00"
    assert res["status"] == "active"

    # Partial pick (5 of 10) does NOT complete the list.
    mp = call_action(inv.mark_picked, conn, _ns(
        pick_list_id=pl["pick_list_id"], item_id=env["item1"], picked_qty="5"))
    assert is_ok(mp)
    assert mp["fully_picked"] is False
    assert conn.execute("SELECT status FROM pick_list WHERE id = ?",
                        (pl["pick_list_id"],)).fetchone()["status"] == "submitted"

    # Full pick (10 of 10) flips the list to 'picked'.
    mp2 = call_action(inv.mark_picked, conn, _ns(
        pick_list_id=pl["pick_list_id"], item_id=env["item1"], picked_qty="10"))
    assert is_ok(mp2)
    assert mp2["fully_picked"] is True
    assert mp2["pick_list_status"] == "picked"

    # Complete → reservation flips to 'consumed', list 'completed', DN generated.
    comp = call_action(inv.complete_pick_list, conn, _ns(id=pl["pick_list_id"]))
    assert is_ok(comp), comp
    assert comp["reservations_consumed"] == 1
    assert comp["delivery_note_id"] is not None  # cross-skill DN created from the SO
    assert conn.execute("SELECT status FROM stock_reservation_entry "
                        "WHERE voucher_id = ?", (pl["pick_list_id"],)).fetchone()["status"] == "consumed"
    assert conn.execute("SELECT status FROM pick_list WHERE id = ?",
                        (pl["pick_list_id"],)).fetchone()["status"] == "completed"


def test_pick_list_release_on_cancel(conn, env):
    """Cancelling a submitted pick list releases its reservation (active→released)."""
    so = _seed_so(conn, env, env["item1"], "10")
    pl = call_action(inv.create_pick_list, conn, _ns(sales_order_id=so))
    call_action(inv.submit_pick_list, conn, _ns(id=pl["pick_list_id"]))

    c = call_action(inv.cancel_pick_list, conn, _ns(id=pl["pick_list_id"], reason="oops"))
    assert is_ok(c)
    assert c["reservations_released"] == 1
    row = conn.execute("SELECT status, released_at FROM stock_reservation_entry "
                       "WHERE voucher_id = ?", (pl["pick_list_id"],)).fetchone()
    assert row["status"] == "released"
    assert row["released_at"] is not None


def test_submit_pick_list_blocked_when_over_available(conn, env):
    """A pick list whose expected qty exceeds available qty cannot be submitted."""
    # item1 has 100 stock. Reserve 95 manually → only 5 available.
    call_action(inv.add_reservation, conn, _ns(
        voucher_type="manual", item_id=env["item1"], warehouse_id=env["warehouse"], qty="95"))
    # SO wants 10 (> 5 available) → submit must block.
    so = _seed_so(conn, env, env["item1"], "10")
    pl = call_action(inv.create_pick_list, conn, _ns(sales_order_id=so))
    sub = call_action(inv.submit_pick_list, conn, _ns(id=pl["pick_list_id"]))
    assert is_error(sub)
    assert "available" in sub["message"].lower()
    # No reservation was created for the blocked pick list (single transaction).
    n = conn.execute("SELECT COUNT(*) AS n FROM stock_reservation_entry "
                     "WHERE voucher_type = 'pick_list' AND voucher_id = ?",
                     (pl["pick_list_id"],)).fetchone()["n"]
    assert n == 0


# ── Reservation math ────────────────────────────────────────────────────────

def test_overlapping_reservations_available_math(conn, env):
    """available = actual - SUM(active reserved). Two active reservations stack."""
    # item1 has 100 stock.
    call_action(inv.add_reservation, conn, _ns(
        voucher_type="manual", item_id=env["item1"], warehouse_id=env["warehouse"], qty="30"))
    call_action(inv.add_reservation, conn, _ns(
        voucher_type="manual", item_id=env["item1"], warehouse_id=env["warehouse"], qty="25"))
    available = inv._available_qty(conn, env["item1"], env["warehouse"])
    assert str(available) == "45.00"  # 100 - 30 - 25


def test_manual_over_reserve_rejected(conn, env):
    """A manual reservation exceeding available qty is rejected."""
    # 100 stock; reserve 60, then try 50 (only 40 left) → reject.
    call_action(inv.add_reservation, conn, _ns(
        voucher_type="manual", item_id=env["item1"], warehouse_id=env["warehouse"], qty="60"))
    res = call_action(inv.add_reservation, conn, _ns(
        voucher_type="manual", item_id=env["item1"], warehouse_id=env["warehouse"], qty="50"))
    assert is_error(res)
    assert "available" in res["message"].lower()


def test_release_reservation_frees_qty(conn, env):
    """Releasing an active reservation restores available qty and is blocked on a
    non-active reservation."""
    r = call_action(inv.add_reservation, conn, _ns(
        voucher_type="manual", item_id=env["item1"], warehouse_id=env["warehouse"], qty="40"))
    assert inv._available_qty(conn, env["item1"], env["warehouse"]).__str__() == "60.00"
    rel = call_action(inv.release_reservation, conn, _ns(id=r["reservation_id"]))
    assert is_ok(rel)
    assert str(inv._available_qty(conn, env["item1"], env["warehouse"])) == "100.00"
    # Re-releasing the now-released reservation is blocked.
    rel2 = call_action(inv.release_reservation, conn, _ns(id=r["reservation_id"]))
    assert is_error(rel2)
    assert "active" in rel2["message"]


def test_material_issue_hard_block_then_release(conn, env):
    """An over-issue against an active reservation is blocked; release lets it
    through the reservation check (full GL submit succeeds, env has stock accounts)."""
    # A material_issue posts COGS; build_inventory_env wires stock + adjustment
    # but not COGS, so add one for the post-release full-submit leg.
    seed_account(conn, env["company_id"], "Cost of Goods Sold", "expense",
                 "cost_of_goods_sold", "5100")
    # Reserve 95 of 100 → available 5.
    r = call_action(inv.add_reservation, conn, _ns(
        voucher_type="manual", item_id=env["item1"], warehouse_id=env["warehouse"], qty="95"))
    # Draft material_issue of 20 (> 5).
    se = _uuid()
    conn.execute(
        "INSERT INTO stock_entry (id, naming_series, stock_entry_type, posting_date, company_id, status) "
        "VALUES (?, 'STE-T', 'material_issue', '2026-06-19', ?, 'draft')",
        (se, env["company_id"]),
    )
    conn.execute(
        "INSERT INTO stock_entry_item (id, stock_entry_id, item_id, quantity, from_warehouse_id, valuation_rate) "
        "VALUES (?, ?, ?, '20', ?, '50')",
        (_uuid(), se, env["item1"], env["warehouse"]),
    )
    conn.commit()
    blocked = call_action(inv.submit_stock_entry, conn, _ns(stock_entry_id=se))
    assert is_error(blocked)
    assert "active reservations" in blocked["message"]
    # No SLE written.
    assert conn.execute("SELECT COUNT(*) AS n FROM stock_ledger_entry WHERE voucher_id = ?",
                        (se,)).fetchone()["n"] == 0

    # Release → the 20 issue now fits (95 freed; available 100). Full GL submit
    # succeeds because build_inventory_env wires stock + COGS accounts.
    call_action(inv.release_reservation, conn, _ns(id=r["reservation_id"]))
    ok = call_action(inv.submit_stock_entry, conn, _ns(stock_entry_id=se))
    assert is_ok(ok), ok
    assert ok["sle_entries_created"] >= 1


# ── get-projected-qty fallback parity ───────────────────────────────────────

def test_projected_qty_fallback_parity(conn, env):
    """With ZERO persisted reservations, get-projected-qty returns the SO-derived
    reserved_qty (back-compat), and the return shape is unchanged."""
    so = _seed_so(conn, env, env["item1"], "12")  # 12 open on the SO
    res = call_action(inv.get_projected_qty, conn, _ns(
        item_id=env["item1"], warehouse_id=env["warehouse"]))
    assert is_ok(res)
    # No persisted reservation rows → fallback to SO-derived (12).
    assert res["reserved_qty"] == "12.00", res
    assert isinstance(res["reserved_qty"], str)  # Decimal-as-text
    # Shape: the four numeric keys all present and string-typed.
    for k in ("actual_qty", "ordered_qty", "reserved_qty", "projected_qty"):
        assert k in res and isinstance(res[k], str)
    # projected = 100 actual + 0 ordered - 12 reserved = 88.
    assert res["projected_qty"] == "88.00", res


def test_projected_qty_persisted_overrides_fallback(conn, env):
    """When a persisted active reservation exists, get-projected-qty reads it
    instead of the SO-derived fallback."""
    so = _seed_so(conn, env, env["item1"], "12")  # SO would derive 12...
    # ...but a persisted manual reservation of 30 takes precedence.
    call_action(inv.add_reservation, conn, _ns(
        voucher_type="manual", item_id=env["item1"], warehouse_id=env["warehouse"], qty="30"))
    res = call_action(inv.get_projected_qty, conn, _ns(
        item_id=env["item1"], warehouse_id=env["warehouse"]))
    assert res["reserved_qty"] == "30.00", res  # persisted, not the SO's 12
    assert res["projected_qty"] == "70.00", res  # 100 - 30
