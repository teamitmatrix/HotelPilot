"""Tests for erpclaw-inventory stock entry lifecycle.

Actions tested: add-stock-entry, get-stock-entry, list-stock-entries,
                submit-stock-entry, cancel-stock-entry
"""
import json
import pytest
from decimal import Decimal
from inventory_helpers import (
    call_action, ns, is_error, is_ok, load_db_query, seed_item, seed_account,
)

mod = load_db_query()


def _items(env, *specs):
    """Build items JSON. Each spec = (item_key, qty, rate, to_wh_key, from_wh_key)."""
    result = []
    for spec in specs:
        item_key, qty, rate = spec[0], spec[1], spec[2]
        entry = {"item_id": env[item_key], "qty": qty, "rate": rate}
        if len(spec) > 3 and spec[3]:
            entry["to_warehouse_id"] = env[spec[3]]
        if len(spec) > 4 and spec[4]:
            entry["from_warehouse_id"] = env[spec[4]]
        result.append(entry)
    return json.dumps(result)


def env_with(env, item_id):
    """Return a shallow copy of env with `xitem` bound to a specific item id, so
    the _items() helper can reference a freshly-seeded item by the 'xitem' key."""
    e = dict(env)
    e["xitem"] = item_id
    return e


def _create_draft_se(conn, env, entry_type="receive", items_str=None):
    """Create a draft stock entry."""
    if not items_str:
        if entry_type == "receive":
            items_str = _items(env, ("item1", "10", "50.00", "warehouse", None))
        elif entry_type == "issue":
            items_str = _items(env, ("item1", "5", "50.00", None, "warehouse"))
        elif entry_type == "transfer":
            items_str = _items(env, ("item1", "5", "50.00", "warehouse2", "warehouse"))
    result = call_action(mod.add_stock_entry, conn, ns(
        entry_type=entry_type, company_id=env["company_id"],
        posting_date="2026-06-15", items=items_str,
    ))
    return result


class TestAddStockEntry:
    def test_receive(self, conn, env):
        result = _create_draft_se(conn, env, "receive")
        assert is_ok(result)
        assert "stock_entry_id" in result
        assert Decimal(result["total_incoming_value"]) == Decimal("500.00")

    def test_issue(self, conn, env):
        result = _create_draft_se(conn, env, "issue")
        assert is_ok(result)
        assert Decimal(result["total_outgoing_value"]) == Decimal("250.00")

    def test_transfer(self, conn, env):
        result = _create_draft_se(conn, env, "transfer")
        assert is_ok(result)
        assert Decimal(result["total_incoming_value"]) == Decimal("250.00")
        assert Decimal(result["total_outgoing_value"]) == Decimal("250.00")

    def test_missing_type_fails(self, conn, env):
        result = call_action(mod.add_stock_entry, conn, ns(
            entry_type=None, company_id=env["company_id"],
            posting_date="2026-06-15",
            items=_items(env, ("item1", "10", "50.00", "warehouse", None)),
        ))
        assert is_error(result)

    def test_invalid_type_fails(self, conn, env):
        result = call_action(mod.add_stock_entry, conn, ns(
            entry_type="invalid", company_id=env["company_id"],
            posting_date="2026-06-15",
            items=_items(env, ("item1", "10", "50.00", "warehouse", None)),
        ))
        assert is_error(result)

    def test_missing_items_fails(self, conn, env):
        result = call_action(mod.add_stock_entry, conn, ns(
            entry_type="receive", company_id=env["company_id"],
            posting_date="2026-06-15", items=None,
        ))
        assert is_error(result)


class TestGetStockEntry:
    def test_get(self, conn, env):
        se = _create_draft_se(conn, env)
        result = call_action(mod.get_stock_entry, conn, ns(
            stock_entry_id=se["stock_entry_id"],
        ))
        assert is_ok(result)
        assert "items" in result

    def test_get_nonexistent_fails(self, conn, env):
        result = call_action(mod.get_stock_entry, conn, ns(
            stock_entry_id="fake-id",
        ))
        assert is_error(result)


class TestListStockEntries:
    def test_list(self, conn, env):
        _create_draft_se(conn, env)
        result = call_action(mod.list_stock_entries, conn, ns(
            company_id=env["company_id"], entry_type=None,
            se_status=None, from_date=None, to_date=None,
            limit=None, offset=None,
        ))
        assert is_ok(result)
        assert result["total_count"] >= 1

    def test_list_by_type(self, conn, env):
        _create_draft_se(conn, env, "receive")
        result = call_action(mod.list_stock_entries, conn, ns(
            company_id=env["company_id"], entry_type="receive",
            se_status=None, from_date=None, to_date=None,
            limit=None, offset=None,
        ))
        assert is_ok(result)
        assert result["total_count"] >= 1


class TestSubmitStockEntry:
    def test_submit_receive(self, conn, env):
        se = _create_draft_se(conn, env, "receive")
        result = call_action(mod.submit_stock_entry, conn, ns(
            stock_entry_id=se["stock_entry_id"],
        ))
        assert is_ok(result)

        row = conn.execute("SELECT status FROM stock_entry WHERE id=?",
                           (se["stock_entry_id"],)).fetchone()
        assert row["status"] == "submitted"

        # Check SLE entries were created
        sle_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM stock_ledger_entry WHERE voucher_id=?",
            (se["stock_entry_id"],)
        ).fetchone()["cnt"]
        assert sle_count >= 1

        # FINDING-009: a valued receipt must ALWAYS post its perpetual-inventory GL.
        # receive default = item1 qty 10 @ 50.00 -> value 500.00.
        gl_rows = conn.execute(
            "SELECT account_id, debit, credit, voucher_type, voucher_id "
            "FROM gl_entry WHERE voucher_id=? AND voucher_type='stock_entry'",
            (se["stock_entry_id"],)
        ).fetchall()
        assert len(gl_rows) == 2, "receipt must post exactly two GL legs"

        debit_legs = {r["account_id"]: r for r in gl_rows if Decimal(r["debit"]) != 0}
        credit_legs = {r["account_id"]: r for r in gl_rows if Decimal(r["credit"]) != 0}

        # DR Stock-in-Hand 500.00
        assert env["stock_acct"] in debit_legs
        dr = debit_legs[env["stock_acct"]]
        assert Decimal(dr["debit"]) == Decimal("500.00")
        assert Decimal(dr["credit"]) == Decimal("0")

        # CR Stock Received Not Billed 500.00
        assert env["srnb"] in credit_legs
        cr = credit_legs[env["srnb"]]
        assert Decimal(cr["credit"]) == Decimal("500.00")
        assert Decimal(cr["debit"]) == Decimal("0")

        # Same voucher on both legs
        for r in gl_rows:
            assert r["voucher_type"] == "stock_entry"
            assert r["voucher_id"] == se["stock_entry_id"]

        # Balanced
        total_dr = sum(Decimal(r["debit"]) for r in gl_rows)
        total_cr = sum(Decimal(r["credit"]) for r in gl_rows)
        assert total_dr == total_cr == Decimal("500.00")

    def test_submit_receive_no_srnb_fails(self, conn, env):
        """FINDING-009 negative: a receipt with no SRNB account must fail loudly
        (clean JSON error), NOT silently skip GL, and must roll back fully."""
        # Remove the SRNB account so the contra cannot resolve.
        conn.execute(
            "DELETE FROM account WHERE account_type='stock_received_not_billed' "
            "AND company_id=?",
            (env["company_id"],),
        )
        conn.commit()

        se = _create_draft_se(conn, env, "receive")
        result = call_action(mod.submit_stock_entry, conn, ns(
            stock_entry_id=se["stock_entry_id"],
        ))

        # Clean error surfaced, not a silent success.
        assert is_error(result)
        assert "Stock Received Not Billed" in result.get("message", "")

        # Full rollback: no GL, status still draft.
        gl_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM gl_entry WHERE voucher_id=? "
            "AND voucher_type='stock_entry'",
            (se["stock_entry_id"],)
        ).fetchone()["cnt"]
        assert gl_count == 0

        row = conn.execute("SELECT status FROM stock_entry WHERE id=?",
                           (se["stock_entry_id"],)).fetchone()
        assert row["status"] == "draft"

    def test_submit_already_submitted_fails(self, conn, env):
        se = _create_draft_se(conn, env, "receive")
        call_action(mod.submit_stock_entry, conn, ns(
            stock_entry_id=se["stock_entry_id"],
        ))
        result = call_action(mod.submit_stock_entry, conn, ns(
            stock_entry_id=se["stock_entry_id"],
        ))
        assert is_error(result)


class TestExternalReceiptRequiresRate:
    """FINDING-010 / ADR-0014: a standalone material_receipt is a true external
    receipt — it must carry a stated rate (or the item's standard_rate), never
    silently book inventory at $0. Internal moves are unaffected (see transfer)."""

    def test_rateless_receipt_no_standard_rate_refuses_loudly(self, conn, env):
        # Fresh item with standard_rate 0 and NO rate on the receipt.
        item = seed_item(conn, "Rate-less Metal", "Each", "stock", "0")
        se = call_action(mod.add_stock_entry, conn, ns(
            entry_type="receive", company_id=env["company_id"],
            posting_date="2026-06-15",
            items=_items(env_with(env, item), ("xitem", "100", "0", "warehouse", None)),
        ))
        assert is_ok(se), f"draft creation should succeed: {se}"
        result = call_action(mod.submit_stock_entry, conn, ns(
            stock_entry_id=se["stock_entry_id"],
        ))

        # Clean error, not a silent $0 success and not an uncaught traceback.
        assert is_error(result)
        assert "no rate was provided" in result.get("message", ""), \
            f"expected actionable rate message, got: {result.get('message')!r}"

        # Full rollback: no SLE, no GL, status still draft (single-transaction submit).
        sle_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM stock_ledger_entry WHERE voucher_id=?",
            (se["stock_entry_id"],)).fetchone()["cnt"]
        assert sle_count == 0
        gl_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM gl_entry WHERE voucher_id=? "
            "AND voucher_type='stock_entry'",
            (se["stock_entry_id"],)).fetchone()["cnt"]
        assert gl_count == 0
        row = conn.execute("SELECT status FROM stock_entry WHERE id=?",
                           (se["stock_entry_id"],)).fetchone()
        assert row["status"] == "draft"

    def test_rateless_receipt_with_standard_rate_succeeds(self, conn, env):
        # Fresh item with standard_rate 6.00, rate-less receipt -> falls back to it.
        item = seed_item(conn, "Standard-cost Metal", "Each", "stock", "6.00")
        se = call_action(mod.add_stock_entry, conn, ns(
            entry_type="receive", company_id=env["company_id"],
            posting_date="2026-06-15",
            items=_items(env_with(env, item), ("xitem", "100", "0", "warehouse", None)),
        ))
        assert is_ok(se)
        result = call_action(mod.submit_stock_entry, conn, ns(
            stock_entry_id=se["stock_entry_id"],
        ))
        assert is_ok(result), f"fallback receipt should succeed: {result}"

        sle = conn.execute(
            "SELECT valuation_rate, stock_value FROM stock_ledger_entry "
            "WHERE voucher_id=? AND item_id=? AND is_cancelled=0",
            (se["stock_entry_id"], item)).fetchone()
        assert Decimal(sle["valuation_rate"]) == Decimal("6.00")
        assert Decimal(sle["stock_value"]) == Decimal("600.00")

        # Balanced inventory GL (DR stock 600 / CR SRNB 600).
        gl_rows = conn.execute(
            "SELECT debit, credit FROM gl_entry WHERE voucher_id=? "
            "AND voucher_type='stock_entry'", (se["stock_entry_id"],)).fetchall()
        total_dr = sum(Decimal(r["debit"]) for r in gl_rows)
        total_cr = sum(Decimal(r["credit"]) for r in gl_rows)
        assert total_dr == total_cr == Decimal("600.00")


class TestInternalTransferRateZeroStillPosts:
    """FINDING-010 / ADR-0014 regression backstop: the require_rate guard must NOT
    fire on internal moves. A material_transfer to an EMPTY destination warehouse at
    rate 0 must still post — it inherits the source valuation. If the guard ever
    leaks onto the transfer-in leg, this fails."""

    def test_transfer_to_empty_warehouse_rate_zero_succeeds(self, conn, env):
        # env item1 already has 100 @ 50.00 in `warehouse`; warehouse2 is empty.
        # The base inventory env omits a COGS account (transfer GL needs one); add
        # it so this isolates the guard behavior, not an unrelated GL-account gap.
        seed_account(conn, env["company_id"], "COGS", "expense",
                     "cost_of_goods_sold", "5100")
        se = call_action(mod.add_stock_entry, conn, ns(
            entry_type="transfer", company_id=env["company_id"],
            posting_date="2026-06-15",
            items=_items(env, ("item1", "10", "0", "warehouse2", "warehouse")),
        ))
        assert is_ok(se), f"transfer draft should succeed: {se}"
        result = call_action(mod.submit_stock_entry, conn, ns(
            stock_entry_id=se["stock_entry_id"],
        ))
        assert is_ok(result), f"rate-0 internal transfer must still post: {result}"

        # IN leg at warehouse2 inherited the 50.00 valuation (not refused, not $0).
        in_leg = conn.execute(
            "SELECT actual_qty, valuation_rate FROM stock_ledger_entry "
            "WHERE voucher_id=? AND warehouse_id=? AND is_cancelled=0",
            (se["stock_entry_id"], env["warehouse2"])).fetchone()
        assert in_leg is not None, "transfer-in leg must exist"
        assert Decimal(in_leg["actual_qty"]) == Decimal("10")
        assert Decimal(in_leg["valuation_rate"]) == Decimal("50.00")


class TestCancelStockEntry:
    def test_cancel(self, conn, env):
        se = _create_draft_se(conn, env, "receive")
        call_action(mod.submit_stock_entry, conn, ns(
            stock_entry_id=se["stock_entry_id"],
        ))
        result = call_action(mod.cancel_stock_entry, conn, ns(
            stock_entry_id=se["stock_entry_id"],
        ))
        assert is_ok(result)

        row = conn.execute("SELECT status FROM stock_entry WHERE id=?",
                           (se["stock_entry_id"],)).fetchone()
        assert row["status"] == "cancelled"

    def test_cancel_nonexistent_fails(self, conn, env):
        result = call_action(mod.cancel_stock_entry, conn, ns(
            stock_entry_id="fake-id",
        ))
        assert is_error(result)
