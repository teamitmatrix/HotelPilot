"""Plan and execute assistant tools for a user message."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .format import format_tool_result_block
from .handlers import HANDLERS
from .registry import ToolSpec, get_tool, tools_for_scope

log = logging.getLogger("webclaw.chat.tools")


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolRunResult:
    calls: list[ToolCall]
    results: list[dict[str, Any]]
    assistant_text: str
    used_tools: bool


def _build_intent_patterns() -> list[tuple[str, re.Pattern[str], Any]]:
    def args_daily(msg: str, ctx: dict) -> dict:
        m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", msg)
        out: dict[str, Any] = {}
        if m:
            out["date"] = m.group(1)
        elif re.search(r"\btoday\b", msg, re.I):
            out["date"] = datetime.now(timezone.utc).date().isoformat()
        hotel = ctx.get("hotel_id") or ctx.get("current_branch")
        if hotel:
            out["hotel_id"] = hotel
        return out

    def args_search(msg: str, ctx: dict) -> dict:
        out: dict[str, Any] = {}
        room = re.search(
            r"\broom\s*(?:number\s*)?#?([A-Za-z0-9\-]+)\b", msg, re.I
        )
        if room:
            out["room_number"] = room.group(1)
        guest = re.search(
            r"\b(?:guest|for|named?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", msg
        )
        if guest:
            out["guest_name"] = guest.group(1)
        else:
            # fallback: quoted name
            q = re.search(r"[\"']([^\"']+)[\"']", msg)
            if q:
                out["guest_name"] = q.group(1)
        return out

    def args_maint(msg: str, ctx: dict) -> dict:
        room = re.search(
            r"\broom\s*(?:number\s*)?#?([A-Za-z0-9\-]+)\b", msg, re.I
        )
        room_id = room.group(1) if room else ""
        # description = message minus the create/maintenance boilerplate
        desc = re.sub(
            r"\b(create|open|file|register|add)\b.*?\b(maintenance|ticket|work\s*order)\b",
            "",
            msg,
            flags=re.I,
        ).strip(" :,-")
        if not desc or len(desc) < 5:
            desc = msg.strip()
        return {"room_id": room_id or "unknown", "description": desc}

    def args_metrics(msg: str, ctx: dict) -> dict:
        if re.search(r"\btoday\b", msg, re.I):
            return {"timeframe": "today"}
        if re.search(r"\bweek\b", msg, re.I):
            return {"timeframe": "week"}
        if re.search(r"\bmonth\b", msg, re.I):
            return {"timeframe": "month"}
        return {"timeframe": "all"}

    return [
        (
            "get_daily_summary",
            re.compile(
                r"\b(daily\s+summary|today'?s\s+summary|ops\s+summary|"
                r"occupancy\s+today|revenue\s+today|pending\s+check[- ]?ins)\b",
                re.I,
            ),
            args_daily,
        ),
        (
            "search_reservations",
            re.compile(
                r"\b(search|find|look\s*up|show)\b.*\b(reservation|booking|guest)\b|"
                r"\b(reservation|booking)\b.*\b(for|guest|room)\b",
                re.I,
            ),
            args_search,
        ),
        (
            "create_maintenance_ticket",
            re.compile(
                r"\b(create|open|file|register|add)\b.*\b(maintenance|work\s*order|ticket)\b|"
                r"\bmaintenance\b.*\broom\b",
                re.I,
            ),
            args_maint,
        ),
        (
            "get_tenant_metrics",
            re.compile(
                r"\b(tenant\s+metrics|platform\s+metrics|subscription\s+status|"
                r"active\s+properties|cross[- ]tenant\s+revenue|mrr)\b",
                re.I,
            ),
            args_metrics,
        ),
        (
            "get_system_health",
            re.compile(
                r"\b(system\s+health|worker\s+status|db\s+pool|sync\s+errors|"
                r"platform\s+health|is\s+the\s+system\s+(ok|up|healthy))\b",
                re.I,
            ),
            lambda m, c: {},
        ),
    ]


def plan_tool_calls(message: str, context: dict[str, Any]) -> list[ToolCall]:
    """Heuristic planner — maps clear intents to allowed tools for this scope."""
    allowed = {t.name for t in tools_for_scope(context)}
    calls: list[ToolCall] = []
    for name, pattern, arg_fn in _build_intent_patterns():
        if name not in allowed:
            continue
        if pattern.search(message or ""):
            args = arg_fn(message or "", context) or {}
            # Skip maintenance without a room reference
            if name == "create_maintenance_ticket" and (
                not args.get("room_id") or args.get("room_id") == "unknown"
            ):
                continue
            calls.append(ToolCall(name=name, arguments=args))
    return calls


async def execute_tool_call(
    call: ToolCall, context: dict[str, Any]
) -> dict[str, Any]:
    spec = get_tool(call.name)
    if not spec:
        return {
            "ok": False,
            "error": f"Unknown tool: {call.name}",
            "display": {
                "type": "status",
                "title": "Unknown tool",
                "body": call.name,
            },
        }
    allowed = {t.name for t in tools_for_scope(context)}
    if call.name not in allowed:
        return {
            "ok": False,
            "error": "Tool not allowed in this session scope",
            "display": {
                "type": "status",
                "title": "Permission denied",
                "body": f"`{call.name}` is not available in the current view.",
            },
        }
    handler = HANDLERS.get(spec.handler)
    if not handler:
        return {
            "ok": False,
            "error": "Handler missing",
            "display": {
                "type": "status",
                "title": "Tool misconfigured",
                "body": call.name,
            },
        }
    try:
        return await handler(call.arguments or {}, context)
    except Exception as e:
        log.exception("Tool %s failed", call.name)
        return {
            "ok": False,
            "error": str(e),
            "display": {
                "type": "status",
                "title": f"{call.name} failed",
                "body": str(e)[:300],
            },
        }


async def run_tools_for_message(
    message: str, context: dict[str, Any]
) -> ToolRunResult:
    """Plan + execute tools; return UI blocks + markdown for the assistant reply."""
    calls = plan_tool_calls(message, context)
    if not calls:
        return ToolRunResult(calls=[], results=[], assistant_text="", used_tools=False)

    results: list[dict[str, Any]] = []
    blocks: list[str] = [
        "Here are the results from the assistant tools:",
    ]
    for call in calls:
        result = await execute_tool_call(call, context)
        results.append({"tool": call.name, "result": result})
        # Structured block for UI cards/tables (preferred rendering)
        blocks.append(format_tool_result_block(call.name, result))

    assistant_text = "\n\n".join(blocks)
    return ToolRunResult(
        calls=calls,
        results=results,
        assistant_text=assistant_text,
        used_tools=True,
    )


def tool_catalog_prompt(context: dict[str, Any]) -> str:
    """Inject available tool names into the system prompt."""
    tools = tools_for_scope(context)
    if not tools:
        return ""
    lines = [
        "\n**Callable tools available in this session:**",
        "When the user asks for these actions, the system may execute tools automatically.",
    ]
    for t in tools:
        lines.append(f"- `{t.name}`: {t.description}")
    return "\n".join(lines)
