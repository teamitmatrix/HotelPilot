"""Unit tests for AI Assistant tool planning / scope gating."""
from chat.tools.registry import tools_for_scope
from chat.tools.runner import plan_tool_calls


def test_hotel_scope_tools():
    names = {t.name for t in tools_for_scope({"viewMode": "hotel_ops", "hotel_id": "h1"})}
    assert "get_daily_summary" in names
    assert "search_reservations" in names
    assert "create_maintenance_ticket" in names
    assert "get_tenant_metrics" not in names
    assert "get_system_health" not in names


def test_superadmin_scope_tools():
    names = {
        t.name
        for t in tools_for_scope(
            {
                "is_superadmin": True,
                "viewMode": "platform_superadmin",
                "tenant": {"is_superadmin": True, "impersonating": False},
            }
        )
    }
    assert "get_tenant_metrics" in names
    assert "get_system_health" in names
    assert "get_daily_summary" not in names


def test_plan_daily_summary():
    calls = plan_tool_calls(
        "Show me today's daily summary",
        {"viewMode": "hotel_ops", "hotel_id": "h1", "current_branch": "h1"},
    )
    assert any(c.name == "get_daily_summary" for c in calls)


def test_plan_system_health_superadmin_only():
    hotel_calls = plan_tool_calls(
        "What is the system health?",
        {"viewMode": "hotel_ops"},
    )
    assert hotel_calls == []

    admin_calls = plan_tool_calls(
        "What is the system health?",
        {
            "is_superadmin": True,
            "viewMode": "platform_superadmin",
            "tenant": {"is_superadmin": True, "impersonating": False},
        },
    )
    assert any(c.name == "get_system_health" for c in admin_calls)


def test_plan_maintenance_needs_room():
    calls = plan_tool_calls(
        "Create a maintenance ticket for room 204 — AC not cooling",
        {"viewMode": "hotel_ops", "hotel_id": "h1"},
    )
    assert len(calls) == 1
    assert calls[0].name == "create_maintenance_ticket"
    assert calls[0].arguments.get("room_id") == "204"
