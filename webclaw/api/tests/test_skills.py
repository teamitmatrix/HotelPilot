"""Skill API tests — 8 tests for Sprint A2.

Tests cover: health, build_cli_args, skill name regex, execute_skill timeout,
audit logging on POST, audit skip on GET.
"""
import os
import re
import sys
import uuid
import pytest

# Setup paths before imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pgutil import connect_web, connect_erp

from fastapi.testclient import TestClient
from skills.executor import build_cli_args

# Import the skill name regex from main
SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(setup_test_db):
    """Create a FastAPI test client."""
    from main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Test 1: Health endpoint
# ---------------------------------------------------------------------------

def test_health_endpoint(client):
    """GET /api/v1/health returns 200 with status ok."""
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["service"] == "ocui-api"


# ---------------------------------------------------------------------------
# Test 2: build_cli_args basic
# ---------------------------------------------------------------------------

def test_build_cli_args_basic():
    """build_cli_args converts action + params to correct flag list."""
    args = build_cli_args("list-companies", {"limit": "20", "offset": "0"})
    assert args == ["--action", "list-companies", "--limit", "20", "--offset", "0"]


# ---------------------------------------------------------------------------
# Test 3: build_cli_args booleans
# ---------------------------------------------------------------------------

def test_build_cli_args_booleans():
    """build_cli_args: True -> --flag present, False -> omitted."""
    args = build_cli_args("submit-invoice", {"force": True, "draft": False, "id": "abc"})
    assert "--force" in args
    assert "--draft" not in args
    assert "--id" in args
    assert "abc" in args


def test_build_cli_args_user_confirmed_is_flag_only():
    """user-confirmed must be bare --user-confirmed (no trailing 1)."""
    args = build_cli_args("migrate", {"user-confirmed": True})
    assert args == ["--action", "migrate", "--user-confirmed"]
    assert "1" not in args


# ---------------------------------------------------------------------------
# Test 4: build_cli_args skips underscore and empty params
# ---------------------------------------------------------------------------

def test_build_cli_args_skips_internal():
    """build_cli_args skips _internal params and empty values."""
    args = build_cli_args("get-item", {"_user_id": "123", "name": "Widget", "empty": ""})
    assert "_user_id" not in " ".join(args)
    assert "--name" in args
    assert "--empty" not in args


def test_build_cli_args_skips_group_scope_keys():
    """group-id / property-ids are env-only; never pass as skill CLI flags."""
    args = build_cli_args(
        "list-companies",
        {
            "limit": "0",
            "group-id": "gid",
            "property-ids": "a,b",
            "company-id": "cid",
        },
    )
    assert "--group-id" not in args
    assert "--property-ids" not in args
    assert args[args.index("--company-id") + 1] == "cid"
    assert args[args.index("--limit") + 1] == "0"


def test_build_cli_args_keeps_id_when_only_tenant_keys_present():
    """Tenant isolation keys must not cause --id to be dropped (housekeeping bug)."""
    args = build_cli_args(
        "update-room-status",
        {
            "id": "room-uuid",
            "status": "dirty",
            "company-id": "tenant-uuid",
            "tenant-id": "tenant-uuid",
        },
    )
    assert args[args.index("--id") + 1] == "room-uuid"
    assert "--status" in args


def test_build_cli_args_keeps_id_alongside_room_id():
    """assign-room / check-in send reservation --id plus --room-id FK."""
    args = build_cli_args(
        "assign-room",
        {
            "id": "reservation-uuid",
            "room_id": "room-uuid",
            "company-id": "tenant-uuid",
            "tenant-id": "tenant-uuid",
        },
    )
    assert args[args.index("--id") + 1] == "reservation-uuid"
    assert args[args.index("--room-id") + 1] == "room-uuid"


def test_build_cli_args_keeps_id_alongside_room_type_id():
    """Inventory updates send --id plus --room-type-id."""
    args = build_cli_args(
        "update-room",
        {"id": "room-uuid", "room_type_id": "type-uuid", "room_number": "101"},
    )
    assert args[args.index("--id") + 1] == "room-uuid"
    assert "--room-type-id" in args


def test_build_cli_args_skips_generic_id_when_entity_id_present():
    args = build_cli_args(
        "get-lease",
        {"id": "generic", "lease-id": "lease-1", "company-id": "t1"},
    )
    assert "--lease-id" in args
    assert "--id" not in args


# ---------------------------------------------------------------------------
# Test 5: Skill name regex accepts valid names
# ---------------------------------------------------------------------------

def test_skill_name_regex_accepts():
    """Valid skill names pass the regex."""
    assert SKILL_NAME_RE.match("my-skill-1")
    assert SKILL_NAME_RE.match("erpclaw")
    assert SKILL_NAME_RE.match("ab")


# ---------------------------------------------------------------------------
# Test 6: Skill name regex rejects invalid names
# ---------------------------------------------------------------------------

def test_skill_name_regex_rejects():
    """Invalid skill names are rejected by the regex."""
    assert not SKILL_NAME_RE.match("../../etc")
    assert not SKILL_NAME_RE.match("A-SKILL")
    assert not SKILL_NAME_RE.match("-start-dash")
    assert not SKILL_NAME_RE.match("a")  # too short (need 2+ chars)
    assert not SKILL_NAME_RE.match("a" * 65)  # too long


# ---------------------------------------------------------------------------
# Test: erpclaw is the local engine, not ~/.openclaw/skills/erpclaw
# ---------------------------------------------------------------------------

def test_erpclaw_loads_from_engine_path():
    """erpclaw resolves to ERPCLAW_ENGINE_PATH (pms/erpclaw-engine)."""
    from skills.executor import (
        find_skill_dir,
        find_skill_md,
        find_skill_script,
        iter_skills,
        skill_search_dirs,
    )
    from env_loader import resolve_erpclaw_engine_path

    engine = resolve_erpclaw_engine_path()
    assert engine.endswith("erpclaw-engine")
    assert os.path.isdir(engine)
    assert find_skill_dir("erpclaw") == engine
    assert find_skill_dir("erpclaw-engine") is None
    md = find_skill_md("erpclaw")
    assert md == os.path.join(engine, "SKILL.md")
    assert os.path.isfile(md)
    script = find_skill_script("erpclaw")
    assert script == os.path.join(engine, "scripts", "db_query.py")
    assert os.path.isfile(script)

    names = {name for name, _ in iter_skills()}
    assert "erpclaw" in names
    assert "erpclaw-engine" not in names
    assert "hospitalityclaw" in names

    openclaw_skills = os.path.expanduser("~/.openclaw/skills")
    assert openclaw_skills not in skill_search_dirs()


# ---------------------------------------------------------------------------
# Test 7: Audit log written on POST to skill route (no auth, empty DB)
# ---------------------------------------------------------------------------

def test_audit_log_on_post(client, setup_test_db):
    """POST to a skill route writes an audit_log row (no users = auth bypass)."""
    import time

    # With empty webclaw_user table, auth is bypassed — action will fail (no skill)
    # but audit middleware should still log the POST
    r = client.post("/api/v1/test-skill/add-item", json={"name": "Test"})
    # The response will be an error (skill not found) but that's OK
    assert r.status_code in (200, 400)

    # Give the async audit writer a moment to complete
    time.sleep(0.5)

    conn = connect_web()
    rows = conn.execute("SELECT * FROM audit_log WHERE skill = 'test-skill'").fetchall()
    assert len(rows) >= 1
    row = dict(rows[0])
    assert row["action"] == "add-item"
    assert "POST" in row["description"]


# ---------------------------------------------------------------------------
# Test 8: Audit log NOT written on GET
# ---------------------------------------------------------------------------

def test_audit_log_skip_get(client, setup_test_db):
    """GET requests to skill routes should NOT create audit_log entries."""
    import time

    r = client.get("/api/v1/test-skill/list-items")
    assert r.status_code in (200, 400)

    time.sleep(0.5)

    conn = connect_web()
    rows = conn.execute("SELECT * FROM audit_log WHERE skill = 'test-skill' AND action = 'list-items'").fetchall()
    assert len(rows) == 0
