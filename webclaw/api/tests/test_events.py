"""Tests for SSE event bus and /events endpoint — Sprint B3."""
import asyncio
import json
import os
import sys
import uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from events import publish, subscribe, unsubscribe
import events


# ---------------------------------------------------------------------------
# Unit tests for pub/sub
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscribe_receives_event():
    """Subscriber receives published events."""
    q = await subscribe()
    await publish({"type": "test", "data": "hello"})
    event = q.get_nowait()
    assert event["type"] == "test"
    assert event["data"] == "hello"
    assert "timestamp" in event
    await unsubscribe(q)


@pytest.mark.asyncio
async def test_multiple_subscribers():
    """All subscribers receive the same event."""
    q1 = await subscribe()
    q2 = await subscribe()
    await publish({"type": "multi-test"})
    e1 = q1.get_nowait()
    e2 = q2.get_nowait()
    assert e1["type"] == "multi-test"
    assert e2["type"] == "multi-test"
    await unsubscribe(q1)
    await unsubscribe(q2)


@pytest.mark.asyncio
async def test_unsubscribe_stops_events():
    """Unsubscribed queue no longer receives events."""
    q = await subscribe()
    await unsubscribe(q)
    await publish({"type": "after-unsub"})
    assert q.empty()


@pytest.mark.asyncio
async def test_emit_schema_update():
    """emit_schema_update sends schema-update event."""
    q = await subscribe()
    await events.emit_schema_update("erpclaw")
    event = q.get_nowait()
    assert event["type"] == "schema-update"
    assert event["skill"] == "erpclaw"
    await unsubscribe(q)


@pytest.mark.asyncio
async def test_emit_data_change():
    """emit_data_change sends data-change event with entity."""
    q = await subscribe()
    await events.emit_data_change("erpclaw", "sales_invoice", scope="id", id="INV-001")
    event = q.get_nowait()
    assert event["type"] == "data-change"
    assert event["skill"] == "erpclaw"
    assert event["entity"] == "sales_invoice"
    assert event["id"] == "INV-001"
    await unsubscribe(q)


@pytest.mark.asyncio
async def test_emit_job_status():
    """emit_job_status sends job-status event."""
    q = await subscribe()
    await events.emit_job_status("job-123", "completed", result={"count": 5})
    event = q.get_nowait()
    assert event["type"] == "job-status"
    assert event["job_id"] == "job-123"
    assert event["result"]["count"] == 5
    await unsubscribe(q)


# ---------------------------------------------------------------------------
# /api/v1/events endpoint test (basic)
# ---------------------------------------------------------------------------


def test_events_endpoint_requires_auth():
    """GET /events without token returns error."""
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    # Without token — the SSE endpoint should return 401
    r = client.get("/api/v1/events")
    assert r.status_code == 401
