"""Tool schemas and scope gating for the AI Assistant."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

Scope = Literal["hotel", "superadmin", "both"]

ToolHandler = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    scope: Scope
    handler: str  # key into HANDLERS map


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="get_daily_summary",
        description=(
            "Hotel daily ops summary: total revenue, occupied rooms, "
            "pending check-ins, and rooms in maintenance for a date."
        ),
        parameters={
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "ISO date YYYY-MM-DD (defaults to today)",
                },
                "hotel_id": {
                    "type": "string",
                    "description": "Hotel/property id (defaults to active branch)",
                },
            },
            "required": [],
        },
        scope="hotel",
        handler="get_daily_summary",
    ),
    ToolSpec(
        name="search_reservations",
        description=(
            "Search hotel reservations by guest name and/or room number. "
            "Returns booking details and check-in/check-out status."
        ),
        parameters={
            "type": "object",
            "properties": {
                "guest_name": {
                    "type": "string",
                    "description": "Guest first or last name fragment",
                },
                "room_number": {
                    "type": "string",
                    "description": "Room number to match",
                },
            },
            "required": [],
        },
        scope="hotel",
        handler="search_reservations",
    ),
    ToolSpec(
        name="create_maintenance_ticket",
        description=(
            "Register a maintenance task for a room (marks room out of order / "
            "maintenance for housekeeping and engineering)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "room_id": {
                    "type": "string",
                    "description": "Room UUID, or room number if id unknown",
                },
                "description": {
                    "type": "string",
                    "description": "Issue description for the ticket",
                },
            },
            "required": ["room_id", "description"],
        },
        scope="hotel",
        handler="create_maintenance_ticket",
    ),
    ToolSpec(
        name="get_tenant_metrics",
        description=(
            "Platform-wide tenant metrics: subscription status, active properties, "
            "and revenue across all hotels."
        ),
        parameters={
            "type": "object",
            "properties": {
                "timeframe": {
                    "type": "string",
                    "description": "today | week | month | all (default: all)",
                    "enum": ["today", "week", "month", "all"],
                },
            },
            "required": [],
        },
        scope="superadmin",
        handler="get_tenant_metrics",
    ),
    ToolSpec(
        name="get_system_health",
        description=(
            "Platform system health: background workers, database connectivity, "
            "API latency, and sync/degraded status."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        scope="superadmin",
        handler="get_system_health",
    ),
]


def _is_superadmin_context(context: dict[str, Any]) -> bool:
    tenant = context.get("tenant") or {}
    if tenant.get("is_superadmin") and not tenant.get("impersonating"):
        return True
    if context.get("is_superadmin") and context.get("viewMode") == "platform_superadmin":
        return True
    return False


def tools_for_scope(context: dict[str, Any]) -> list[ToolSpec]:
    """Return tools allowed for the current session scope."""
    if _is_superadmin_context(context):
        return [t for t in TOOL_SPECS if t.scope in ("superadmin", "both")]
    return [t for t in TOOL_SPECS if t.scope in ("hotel", "both")]


def openai_tool_schemas(context: dict[str, Any]) -> list[dict[str, Any]]:
    """OpenAI-style tools array for the current scope."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools_for_scope(context)
    ]


def get_tool(name: str) -> ToolSpec | None:
    for t in TOOL_SPECS:
        if t.name == name:
            return t
    return None
