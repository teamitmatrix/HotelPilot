"""Template rendering for guest notifications."""
from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render_template(template: str, context: dict[str, Any]) -> str:
    """Replace ``{{placeholder}}`` tokens; unknown keys become empty string."""
    if not template:
        return ""

    def repl(match: re.Match) -> str:
        key = match.group(1)
        val = context.get(key)
        if val is None:
            return ""
        return str(val)

    return _PLACEHOLDER_RE.sub(repl, template)


def sanitize_context(context: dict[str, Any]) -> dict[str, str]:
    """Normalize notification context values to strings."""
    out: dict[str, str] = {}
    for key, val in (context or {}).items():
        if val is None:
            out[key] = ""
        else:
            out[key] = str(val)
    return out
