"""Serialize tool results into chat-embeddable <tool_result> blocks + markdown."""
from __future__ import annotations

import json
from typing import Any


def format_tool_result_block(tool_name: str, result: dict[str, Any]) -> str:
    """Emit a structured block the chat UI can render as cards/tables."""
    display = result.get("display") or {
        "type": "status",
        "title": tool_name,
        "body": json.dumps(result, default=str)[:1500],
    }
    payload = {
        "tool": tool_name,
        "ok": bool(result.get("ok", True)),
        **display,
    }
    return f"<tool_result>{json.dumps(payload, default=str)}</tool_result>"


def format_tool_markdown_summary(tool_name: str, result: dict[str, Any]) -> str:
    """Human-readable markdown companion (for LLM context / fallback rendering)."""
    display = result.get("display") or {}
    dtype = display.get("type")
    title = display.get("title") or tool_name
    lines = [f"**{title}**"]

    if dtype == "metrics":
        for m in display.get("metrics") or []:
            lines.append(f"- {m.get('label')}: **{m.get('value')}**")
        footer = display.get("footer_table")
        if footer:
            lines.append("")
            lines.append(f"*{footer.get('title') or 'Details'}*")
            for row in footer.get("rows") or []:
                lines.append(f"- {', '.join(str(c) for c in row)}")
    elif dtype == "table":
        cols = display.get("columns") or []
        rows = display.get("rows") or []
        if not rows:
            lines.append(display.get("empty") or "_No rows_")
        else:
            lines.append("| " + " | ".join(str(c) for c in cols) + " |")
            lines.append("| " + " | ".join("---" for _ in cols) + " |")
            for row in rows[:15]:
                lines.append("| " + " | ".join(str(c) for c in row) + " |")
    else:
        body = display.get("body") or result.get("error") or ""
        if body:
            lines.append(str(body))

    return "\n".join(lines)
