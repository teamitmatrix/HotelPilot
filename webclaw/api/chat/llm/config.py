"""Environment-driven LLM provider / model mapping.

Recommended cost/quality defaults:

  PRIMARY   → gemini-2.0-flash   (fast, cheap, strong quality)
  ANALYTICS → gpt-4o             (reasoning / summaries)
  REPORT    → claude-3-5-sonnet  (structured long-form)
  FALLBACK  → gpt-4o-mini        (cheap OpenAI backup)

Env example:

  PRIMARY_LLM_PROVIDER=gemini
  PRIMARY_LLM_MODEL=gemini-2.0-flash
  ANALYTICS_LLM_PROVIDER=openai
  ANALYTICS_LLM_MODEL=gpt-4o
  REPORT_LLM_PROVIDER=anthropic
  REPORT_LLM_MODEL=claude-3-5-sonnet-20241022
  FALLBACK_LLM_PROVIDER=openai
  FALLBACK_LLM_MODEL=gpt-4o-mini

  OPENAI_API_KEY=...
  ANTHROPIC_API_KEY=...
  GEMINI_API_KEY=...
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .types import ModelRef, ProviderName, TaskKind


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _parse_provider(raw: str, default: ProviderName) -> ProviderName:
    value = (raw or default.value).strip().lower()
    aliases = {
        "gpt": ProviderName.OPENAI,
        "claude": ProviderName.ANTHROPIC,
        "google": ProviderName.GEMINI,
        "gemini": ProviderName.GEMINI,
    }
    if value in aliases:
        return aliases[value]
    try:
        return ProviderName(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class LLMSettings:
    primary: ModelRef
    analytics: ModelRef
    report: ModelRef
    fallback: ModelRef
    openai_api_key: str
    openai_base_url: str
    anthropic_api_key: str
    gemini_api_key: str
    gemini_base_url: str
    max_tokens: int = 4096


def load_llm_settings() -> LLMSettings:
    """Load provider routing settings from the environment."""
    has_gemini = bool(_env("GEMINI_API_KEY"))
    has_openai = bool(_env("OPENAI_API_KEY"))

    # Prefer Gemini Flash for routine work when configured (best $/quality).
    if has_gemini:
        default_primary = ProviderName.GEMINI
        default_primary_model = "gemini-2.0-flash"
    else:
        default_primary = ProviderName.OPENAI
        default_primary_model = "gpt-4o-mini"

    default_analytics_provider = (
        ProviderName.OPENAI if has_openai else default_primary
    )
    default_analytics_model = "gpt-4o" if has_openai else default_primary_model

    default_report_provider = (
        ProviderName.ANTHROPIC
        if _env("ANTHROPIC_API_KEY")
        else default_analytics_provider
    )
    default_report_model = (
        "claude-3-5-sonnet-20241022"
        if _env("ANTHROPIC_API_KEY")
        else default_analytics_model
    )

    if has_openai:
        default_fallback_provider = ProviderName.OPENAI
        default_fallback_model = "gpt-4o-mini"
    elif has_gemini:
        default_fallback_provider = ProviderName.GEMINI
        default_fallback_model = "gemini-2.0-flash"
    else:
        default_fallback_provider = default_primary
        default_fallback_model = default_primary_model

    primary = ModelRef(
        _parse_provider(_env("PRIMARY_LLM_PROVIDER"), default_primary),
        _env("PRIMARY_LLM_MODEL", default_primary_model),
    )
    analytics = ModelRef(
        _parse_provider(_env("ANALYTICS_LLM_PROVIDER"), default_analytics_provider),
        _env("ANALYTICS_LLM_MODEL", default_analytics_model),
    )
    report = ModelRef(
        _parse_provider(_env("REPORT_LLM_PROVIDER"), default_report_provider),
        _env("REPORT_LLM_MODEL", default_report_model),
    )
    fallback = ModelRef(
        _parse_provider(_env("FALLBACK_LLM_PROVIDER"), default_fallback_provider),
        _env("FALLBACK_LLM_MODEL", default_fallback_model),
    )

    return LLMSettings(
        primary=primary,
        analytics=analytics,
        report=report,
        fallback=fallback,
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_base_url=_env("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip(
            "/"
        ),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        gemini_api_key=_env("GEMINI_API_KEY"),
        gemini_base_url=_env(
            "GEMINI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta",
        ).rstrip("/"),
        max_tokens=int(_env("LLM_MAX_TOKENS", "4096") or "4096"),
    )


def model_for_task(settings: LLMSettings, task: TaskKind) -> ModelRef:
    if task == TaskKind.FAST:
        return settings.primary
    if task == TaskKind.REPORT:
        return settings.report
    return settings.analytics
