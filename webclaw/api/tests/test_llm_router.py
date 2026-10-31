"""Tests for multi-LLM classification, config, and failover routing."""
from __future__ import annotations

import os

import pytest

from chat.llm.classify import classify_task
from chat.llm.config import load_llm_settings, model_for_task
from chat.llm.providers import ProviderError
from chat.llm.router import LLMRouter, reset_router
from chat.llm.types import ChatRequest, ModelRef, ProviderName, TaskKind


@pytest.fixture(autouse=True)
def _clear_router():
    reset_router()
    yield
    reset_router()


def test_classify_fast_lookup():
    assert classify_task("What is the status of room 204?") == TaskKind.FAST
    assert classify_task("Find booking BK-123") == TaskKind.FAST


def test_classify_analytics():
    assert (
        classify_task("Analyze occupancy trends vs last month revenue")
        == TaskKind.ANALYTICS
    )


def test_classify_report():
    assert (
        classify_task("Generate a detailed executive summary report for Q3")
        == TaskKind.REPORT
    )
    assert classify_task("hello", history_len=30) == TaskKind.REPORT


def test_classify_override_from_context():
    assert classify_task("anything", context={"task_kind": "fast"}) == TaskKind.FAST


def test_load_settings_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    monkeypatch.setenv("PRIMARY_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("PRIMARY_LLM_MODEL", "gemini-2.0-flash")
    monkeypatch.setenv("ANALYTICS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("ANALYTICS_LLM_MODEL", "gpt-4o")
    monkeypatch.setenv("REPORT_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("REPORT_LLM_MODEL", "claude-3-5-sonnet-20241022")
    monkeypatch.setenv("FALLBACK_LLM_PROVIDER", "openai")
    monkeypatch.setenv("FALLBACK_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

    settings = load_llm_settings()
    assert settings.primary == ModelRef(ProviderName.GEMINI, "gemini-2.0-flash")
    assert settings.analytics.model == "gpt-4o"
    assert settings.report.provider == ProviderName.ANTHROPIC
    assert settings.fallback.provider == ProviderName.OPENAI
    assert model_for_task(settings, TaskKind.FAST).model == "gemini-2.0-flash"
    assert model_for_task(settings, TaskKind.ANALYTICS).model == "gpt-4o"


@pytest.mark.asyncio
async def test_router_failover(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
    monkeypatch.setenv("PRIMARY_LLM_PROVIDER", "openai")
    monkeypatch.setenv("PRIMARY_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("FALLBACK_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("FALLBACK_LLM_MODEL", "gemini-2.0-flash")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    settings = load_llm_settings()
    router = LLMRouter(settings)

    class Boom:
        name = ProviderName.OPENAI

        def available(self):
            return True

        async def complete(self, request, model):
            raise ProviderError("openai", "rate limited", 429)

        async def stream(self, request, model):
            raise ProviderError("openai", "rate limited", 429)
            yield  # pragma: no cover

    class Ok:
        name = ProviderName.GEMINI

        def available(self):
            return True

        async def complete(self, request, model):
            return "fallback-ok"

        async def stream(self, request, model):
            yield "fallback-ok"

    router.providers[ProviderName.OPENAI] = Boom()
    router.providers[ProviderName.GEMINI] = Ok()
    for name in list(router.providers):
        if name not in (ProviderName.OPENAI, ProviderName.GEMINI):
            class Dead:
                name = name

                def available(self):
                    return False

                async def complete(self, request, model):
                    raise ProviderError(name.value, "disabled")

                async def stream(self, request, model):
                    raise ProviderError(name.value, "disabled")
                    yield  # pragma: no cover

            router.providers[name] = Dead()

    req = ChatRequest(
        messages=[{"role": "user", "content": "What is room 101 status?"}],
        system="test",
        max_tokens=64,
    )
    result = await router.complete(req, message="What is room 101 status?")
    assert result.text == "fallback-ok"
    assert result.failover is True
    assert result.model.provider == ProviderName.GEMINI


def test_no_providers_message(monkeypatch):
    for key in list(os.environ):
        if any(
            key.startswith(p)
            for p in (
                "OPENAI_",
                "ANTHROPIC_",
                "GEMINI_",
                "PRIMARY_",
                "ANALYTICS_",
                "REPORT_",
                "FALLBACK_",
            )
        ):
            monkeypatch.delenv(key, raising=False)

    settings = load_llm_settings()
    router = LLMRouter(settings)
    for name in list(router.providers):

        class Dead:
            name = name

            def available(self):
                return False

        router.providers[name] = Dead()

    chain = router._chain(TaskKind.FAST)
    assert chain == []
