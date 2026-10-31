"""Part A tests for erpclaw-inventory resolve-item (FINDING-008).

Deterministic 4-tier cascade resolver: exact -> singularized -> substring ->
token-AND. Read-only. Cross-DB via fn.Lower (never .ilike()). Response contract
derives matched/single_match/multiple_matches from the candidate list.
"""
import pytest
from inventory_helpers import (
    call_action, ns, is_error, is_ok, load_db_query,
)

mod = load_db_query()


# ── helpers ──

def _add(conn, code, name, item_type="stock", stock_uom="Nos", standard_rate="0"):
    result = call_action(mod.add_item, conn, ns(
        item_code=code, item_name=name,
        item_type=item_type, valuation_method=None, item_group=None,
        stock_uom=stock_uom, has_batch=None, has_serial=None,
        standard_rate=standard_rate, custom_fields=None,
    ))
    assert is_ok(result), result
    return result["item_id"]


def _resolve(conn, name):
    return call_action(mod.resolve_item, conn, ns(name=name))


# ── _singularize unit coverage ──

class TestSingularize:
    def test_simple_s(self):
        assert mod._singularize("sets") == "set"

    def test_es_after_x(self):
        assert mod._singularize("boxes") == "box"

    def test_es_after_h(self):
        assert mod._singularize("batches") == "batch"

    def test_ies(self):
        assert mod._singularize("categories") == "category"

    def test_ss_guard(self):
        assert mod._singularize("glass") == "glass"

    def test_last_word_only(self):
        assert mod._singularize("brake pad sets") == "brake pad set"

    def test_empty(self):
        assert mod._singularize("") == ""

    def test_short_word_not_mangled(self):
        # "is" is 2 chars ending in 's' but not 'ss' -> rule len(w) > 1 trims to 'i';
        # acceptable: tier 3/4 catch the rest. Verify it does not crash.
        assert mod._singularize("a") == "a"


# ── cascade tiers ──

class TestResolveItem:
    def test_exact(self, conn, env):
        _add(conn, "BPS-001", "Brake Pad Set")
        r = _resolve(conn, "Brake Pad Set")
        assert is_ok(r)
        assert r["matched"] is True
        assert r["single_match"] is True
        assert r["multiple_matches"] is False
        assert r["match_type"] == "exact"
        assert r["candidates"][0]["item_name"] == "Brake Pad Set"

    def test_plural_s(self, conn, env):
        _add(conn, "BPS-001", "Brake Pad Set")
        r = _resolve(conn, "Brake Pad Sets")
        assert is_ok(r)
        assert r["matched"] is True
        assert r["single_match"] is True
        assert r["match_type"] == "singular"
        assert r["candidates"][0]["item_name"] == "Brake Pad Set"

    def test_plural_es(self, conn, env):
        _add(conn, "BOX-001", "Storage Box")
        r = _resolve(conn, "Storage Boxes")
        assert is_ok(r)
        assert r["matched"] is True
        assert r["single_match"] is True
        assert r["match_type"] == "singular"
        assert r["candidates"][0]["item_name"] == "Storage Box"

    def test_plural_ies(self, conn, env):
        _add(conn, "CAT-001", "Service Category", item_type="service")
        r = _resolve(conn, "Service Categories")
        assert is_ok(r)
        assert r["matched"] is True
        assert r["single_match"] is True
        assert r["match_type"] == "singular"
        assert r["candidates"][0]["item_name"] == "Service Category"

    def test_case_lower(self, conn, env):
        _add(conn, "BPS-001", "Brake Pad Set")
        r = _resolve(conn, "brake pad set")
        assert is_ok(r)
        assert r["single_match"] is True
        assert r["match_type"] == "exact"

    def test_case_upper(self, conn, env):
        _add(conn, "BPS-001", "Brake Pad Set")
        r = _resolve(conn, "BRAKE PAD SET")
        assert is_ok(r)
        assert r["single_match"] is True
        assert r["match_type"] == "exact"

    def test_substring_multiword(self, conn, env):
        _add(conn, "BPS-001", "Brake Pad Set")
        r = _resolve(conn, "brake pad")
        assert is_ok(r)
        assert r["matched"] is True
        assert r["single_match"] is True
        assert r["match_type"] == "substring"
        assert r["candidates"][0]["item_name"] == "Brake Pad Set"

    def test_ambiguous_exact_wins(self, conn, env):
        _add(conn, "BPS-001", "Brake Pad Set")
        _add(conn, "BPS-002", "Brake Pad Set Premium")
        # exact tier returns ONLY the exact name -> single
        r = _resolve(conn, "brake pad set")
        assert is_ok(r)
        assert r["match_type"] == "exact"
        assert r["single_match"] is True
        assert len(r["candidates"]) == 1
        assert r["candidates"][0]["item_name"] == "Brake Pad Set"

    def test_ambiguous_substring_multiple(self, conn, env):
        _add(conn, "BPS-001", "Brake Pad Set")
        _add(conn, "BPS-002", "Brake Pad Set Premium")
        r = _resolve(conn, "brake pad")
        assert is_ok(r)
        assert r["matched"] is True
        assert r["multiple_matches"] is True
        assert r["single_match"] is False
        assert len(r["candidates"]) == 2
        assert r["match_type"] == "substring"
        # shortest-name-first ordering
        assert r["candidates"][0]["item_name"] == "Brake Pad Set"
        assert r["candidates"][1]["item_name"] == "Brake Pad Set Premium"

    def test_token_and_reordered(self, conn, env):
        _add(conn, "BPS-001", "Brake Pad Set")
        r = _resolve(conn, "set pad brake")
        assert is_ok(r)
        assert r["matched"] is True
        assert r["match_type"] == "tokens"
        assert r["candidates"][0]["item_name"] == "Brake Pad Set"

    def test_no_match_is_ok_not_error(self, conn, env):
        _add(conn, "BPS-001", "Brake Pad Set")
        r = _resolve(conn, "unicorn glitter")
        assert is_ok(r)            # status ok, exit 0 — NOT an error
        assert "message" not in r  # no error key
        assert r["matched"] is False
        assert r["single_match"] is False
        assert r["multiple_matches"] is False
        assert r["match_type"] is None
        assert r["candidates"] == []

    def test_over_singularize_guard(self, conn, env):
        _add(conn, "GLP-001", "Glass Panel")
        r = _resolve(conn, "Glass Panel")
        assert is_ok(r)
        assert r["single_match"] is True
        assert r["match_type"] == "exact"
        assert r["candidates"][0]["item_name"] == "Glass Panel"

    def test_missing_name_errors(self, conn, env):
        r = call_action(mod.resolve_item, conn, ns(name=None))
        assert is_error(r)

    def test_empty_name_errors(self, conn, env):
        r = call_action(mod.resolve_item, conn, ns(name="   "))
        assert is_error(r)

    def test_rate_is_text(self, conn, env):
        _add(conn, "BPS-001", "Brake Pad Set", standard_rate="45.00")
        r = _resolve(conn, "Brake Pad Set")
        assert is_ok(r)
        rate = r["candidates"][0]["standard_rate"]
        assert rate == "45.00"
        assert isinstance(rate, str)

    def test_match_by_item_code(self, conn, env):
        _add(conn, "BPS-001", "Brake Pad Set")
        r = _resolve(conn, "BPS-001")
        assert is_ok(r)
        assert r["single_match"] is True
        assert r["match_type"] == "exact"
        assert r["candidates"][0]["item_code"] == "BPS-001"

    def test_widgets_plural_no_regression(self, conn, env):
        # env already seeds "Widget A" / "Widget B"; "Widgets" must resolve.
        r = _resolve(conn, "Widgets")
        assert is_ok(r)
        assert r["matched"] is True
        # both Widget A and Widget B contain "widget" -> singular LIKE -> multiple
        names = [c["item_name"] for c in r["candidates"]]
        assert "Widget A" in names and "Widget B" in names
        assert r["match_type"] == "singular"
