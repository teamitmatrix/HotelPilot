"""Tests for context resolution engine — Sprint C1."""
import json
import os
import sys
import uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pgutil import connect_web, connect_erp

from chat.entity_resolver import resolve_entity
from chat.ai_client import build_system_prompt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def test_resolve_exact_match():
    """Exact name match returns confidence 1.0."""
    results = resolve_entity("customer", "Wayne Enterprises")
    assert len(results) >= 1
    assert results[0]["name"] == "Wayne Enterprises"
    assert results[0]["confidence"] == 1.0


def test_resolve_starts_with():
    """Prefix match returns confidence 0.85."""
    results = resolve_entity("customer", "wayne")
    # Should match Wayne Enterprises and Wayne Tech Labs
    assert len(results) >= 2
    names = [r["name"] for r in results]
    assert "Wayne Enterprises" in names
    assert "Wayne Tech Labs" in names


def test_resolve_contains():
    """Substring match returns confidence 0.65."""
    results = resolve_entity("customer", "tech")
    assert len(results) >= 1
    assert results[0]["name"] == "Wayne Tech Labs"


def test_resolve_across_types():
    """Searching without entity_type searches all tables."""
    results = resolve_entity(None, "arc")
    # Should find items: "Arc Reactor" and "Arc Welding Kit"
    assert len(results) >= 2
    types = {r["entity_type"] for r in results}
    assert "item" in types


def test_resolve_empty_query():
    """Empty query returns empty results."""
    assert resolve_entity("customer", "") == []
    assert resolve_entity("customer", "   ") == []


def test_resolve_no_match():
    """Nonexistent entity returns empty results."""
    results = resolve_entity("customer", "zzz_nonexistent_xyz")
    assert len(results) == 0


def test_resolve_confidence_ordering():
    """Results are sorted by confidence descending."""
    results = resolve_entity(None, "arc")
    if len(results) >= 2:
        for i in range(len(results) - 1):
            assert results[i]["confidence"] >= results[i + 1]["confidence"]


def test_resolve_specific_type():
    """Specifying entity_type limits results to that table."""
    results = resolve_entity("account", "cash")
    assert all(r["entity_type"] == "account" for r in results)
    assert results[0]["name"] == "Cash"


# ---------------------------------------------------------------------------
# System prompt enrichment tests
# ---------------------------------------------------------------------------

def test_system_prompt_basic():
    """Basic system prompt includes skill and entity."""
    prompt = build_system_prompt({"skill": "erpclaw", "entity": "sales_invoice"})
    assert "erpclaw" in prompt
    assert "sales_invoice" in prompt


def test_system_prompt_with_resolved_entities():
    """System prompt includes resolved entities when provided."""
    context = {
        "skill": "erpclaw",
        "resolved_entities": [
            {
                "name": "Wayne Enterprises",
                "entity_type": "customer",
                "id": "c1",
                "confidence": 0.95,
            }
        ],
    }
    prompt = build_system_prompt(context)
    assert "Wayne Enterprises" in prompt
    assert "customer" in prompt
    assert "95%" in prompt


# ---------------------------------------------------------------------------
# API endpoint test
# ---------------------------------------------------------------------------

def test_resolve_entity_endpoint(setup_test_db):
    """POST /chat/resolve-entity returns matches."""
    from auth.passwords import hash_password
    from fastapi.testclient import TestClient
    from main import app

    # Create user + get token
    user_id = str(uuid.uuid4())
    conn = connect_web()
    conn.execute(
        "INSERT INTO webclaw_user (id, username, email, full_name, password_hash, status) VALUES (?, ?, ?, ?, ?, 'active')",
        (user_id, "admin", "admin@test.com", "Admin", hash_password("Pass123!")),
    )
    conn.commit()
    conn.close()

    client = TestClient(app)
    r = client.post("/api/v1/auth/login", json={"email": "admin@test.com", "password": "Pass123!"})
    token = r.json()["access_token"]

    r = client.post(
        "/api/v1/chat/resolve-entity",
        json={"query": "Wayne", "entity_type": "customer"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert len(data["matches"]) >= 1
    assert data["matches"][0]["name"] == "Wayne Enterprises"


def test_resolve_entity_requires_auth():
    """POST /chat/resolve-entity without token returns 401."""
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    r = client.post("/api/v1/chat/resolve-entity", json={"query": "test"})
    assert r.status_code == 401
