"""Heuristic task classification for LLM routing."""
from __future__ import annotations

import re

from .types import TaskKind

_FAST_PATTERNS = re.compile(
    r"\b("
    r"what is|what's|whats|who is|where is|when is|how many|"
    r"status of|look up|lookup|find|show me|list|get|"
    r"room\s*\d+|folio|reservation|booking\s*#?|"
    r"sql|select |extract|parse"
    r")\b",
    re.I,
)

_ANALYTICS_PATTERNS = re.compile(
    r"\b("
    r"analy[sz]e|analytics|trend|compare|comparison|versus|vs\.?|"
    r"summarize|summary|breakdown|metric|kpi|revenue|occupancy|"
    r"forecast|why did|explain|performance|insight|correlation|"
    r"average|median|distribution|cohort"
    r")\b",
    re.I,
)

_REPORT_PATTERNS = re.compile(
    r"\b("
    r"report|detailed report|executive summary|long.?form|"
    r"write a|generate a|draft a|full analysis|deep dive|"
    r"structured report|board pack|monthly review"
    r")\b",
    re.I,
)


def classify_task(
    message: str,
    *,
    history_len: int = 0,
    context: dict | None = None,
) -> TaskKind:
    """Classify a user message into fast / analytics / report buckets.

    Rules (first match wins, with context boosts):
    - Explicit report intents or very long conversations → REPORT
    - Analytical keywords or analytics UI context → ANALYTICS
    - Short lookup-style questions → FAST
    - Default → ANALYTICS for safer reasoning quality
    """
    text = (message or "").strip()
    ctx = context or {}
    lower = text.lower()

    # Explicit task override from client/context
    override = (ctx.get("task_kind") or ctx.get("llm_task") or "").strip().lower()
    if override in ("fast", "lookup", "simple"):
        return TaskKind.FAST
    if override in ("analytics", "analysis"):
        return TaskKind.ANALYTICS
    if override in ("report", "summary_long"):
        return TaskKind.REPORT

    if _REPORT_PATTERNS.search(lower) or history_len >= 24 or len(text) > 1200:
        return TaskKind.REPORT

    if _ANALYTICS_PATTERNS.search(lower):
        return TaskKind.ANALYTICS

    view = (ctx.get("view") or "").lower()
    entity = (ctx.get("entity") or "").lower()
    if any(k in view or k in entity for k in ("analytics", "revenue", "finance")):
        return TaskKind.ANALYTICS

    if len(text) <= 160 and (_FAST_PATTERNS.search(lower) or text.count(" ") < 12):
        return TaskKind.FAST

    if len(text) <= 80:
        return TaskKind.FAST

    return TaskKind.ANALYTICS
