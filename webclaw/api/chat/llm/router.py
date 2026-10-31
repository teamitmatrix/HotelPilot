"""Task-aware LLM router with graceful provider failover."""
from __future__ import annotations

import logging
from typing import AsyncGenerator

from .classify import classify_task
from .config import LLMSettings, load_llm_settings, model_for_task
from .providers import ProviderError, build_providers
from .types import ChatRequest, ChatResult, ModelRef, ProviderName, TaskKind

log = logging.getLogger("webclaw.chat.llm")


class LLMRouter:
    """Routes chat requests to primary/analytics/report models with failover."""

    def __init__(self, settings: LLMSettings | None = None):
        self.settings = settings or load_llm_settings()
        self.providers = build_providers(self.settings)

    def classify(
        self,
        message: str,
        *,
        history_len: int = 0,
        context: dict | None = None,
    ) -> TaskKind:
        return classify_task(message, history_len=history_len, context=context)

    def _chain(self, task: TaskKind) -> list[ModelRef]:
        """Ordered model attempts: task primary → fallback → other configured."""
        primary = model_for_task(self.settings, task)
        seen: set[str] = set()
        chain: list[ModelRef] = []

        def add(ref: ModelRef) -> None:
            key = ref.key()
            if key in seen:
                return
            provider = self.providers.get(ref.provider)
            if not provider or not provider.available():  # type: ignore[union-attr]
                return
            seen.add(key)
            chain.append(ref)

        add(primary)
        add(self.settings.fallback)
        add(self.settings.primary)
        add(self.settings.analytics)
        add(self.settings.report)

        for name, provider in self.providers.items():
            if not provider.available():  # type: ignore[union-attr]
                continue
            default_model = {
                ProviderName.OPENAI: "gpt-4o-mini",
                ProviderName.ANTHROPIC: "claude-3-5-sonnet-20241022",
                ProviderName.GEMINI: "gemini-2.0-flash",
            }.get(name, "default")
            add(ModelRef(name, default_model))

        return chain

    async def complete(
        self,
        request: ChatRequest,
        *,
        task: TaskKind | None = None,
        message: str = "",
        history_len: int = 0,
        context: dict | None = None,
    ) -> ChatResult:
        kind = task or self.classify(
            message or _last_user_text(request),
            history_len=history_len,
            context=context,
        )
        chain = self._chain(kind)
        if not chain:
            raise ProviderError(
                "router",
                "No LLM providers configured. Set OPENAI_API_KEY, ANTHROPIC_API_KEY, "
                "and/or GEMINI_API_KEY.",
            )

        errors: list[str] = []
        for i, ref in enumerate(chain):
            provider = self.providers[ref.provider]
            try:
                text = await provider.complete(request, ref.model)  # type: ignore[union-attr]
                return ChatResult(
                    text=text,
                    model=ref,
                    provider_used=ref.key(),
                    failover=i > 0,
                )
            except ProviderError as e:
                log.warning("LLM complete failed on %s: %s", ref.key(), e)
                errors.append(str(e))
                continue

        raise ProviderError("router", "All providers failed: " + " | ".join(errors[:5]))

    async def stream(
        self,
        request: ChatRequest,
        *,
        task: TaskKind | None = None,
        message: str = "",
        history_len: int = 0,
        context: dict | None = None,
    ) -> AsyncGenerator[str, None]:
        kind = task or self.classify(
            message or _last_user_text(request),
            history_len=history_len,
            context=context,
        )
        chain = self._chain(kind)
        if not chain:
            yield (
                "[Error: No LLM providers configured. Set OPENAI_API_KEY, "
                "ANTHROPIC_API_KEY, and/or GEMINI_API_KEY.]"
            )
            return

        errors: list[str] = []
        for i, ref in enumerate(chain):
            provider = self.providers[ref.provider]
            produced = False
            try:
                async for chunk in provider.stream(request, ref.model):  # type: ignore[union-attr]
                    produced = True
                    yield chunk
                if i > 0:
                    log.info("LLM stream failover succeeded via %s", ref.key())
                return
            except ProviderError as e:
                log.warning("LLM stream failed on %s: %s", ref.key(), e)
                errors.append(str(e))
                if produced:
                    yield f"\n\n[Error: provider {ref.provider.value} failed mid-stream]"
                    return
                continue

        yield "[Error: All LLM providers failed: " + "; ".join(errors[:3]) + "]"


_router: LLMRouter | None = None


def get_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router


def reset_router() -> None:
    """Test helper — clear cached router/settings."""
    global _router
    _router = None


def _last_user_text(request: ChatRequest) -> str:
    for msg in reversed(request.messages):
        if msg.get("role") == "user":
            return msg.get("content") or ""
    return ""
