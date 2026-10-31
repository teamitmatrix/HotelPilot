"""Shared types for the multi-LLM provider layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Protocol


class TaskKind(str, Enum):
    """Complexity / routing bucket for a chat request."""

    FAST = "fast"  # simple lookup / Q&A / SQL extraction
    ANALYTICS = "analytics"  # analytical queries & data summaries
    REPORT = "report"  # long-context structured reports


class ProviderName(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


@dataclass(frozen=True)
class ModelRef:
    """A concrete provider + model pair."""

    provider: ProviderName
    model: str

    def key(self) -> str:
        return f"{self.provider.value}:{self.model}"


@dataclass
class ChatRequest:
    messages: list[dict[str, str]]
    system: str
    max_tokens: int = 4096
    temperature: float = 0.2
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResult:
    text: str
    model: ModelRef
    provider_used: str
    failover: bool = False


class LLMProvider(Protocol):
    name: ProviderName

    def available(self) -> bool:
        """True when credentials / endpoint are configured."""
        ...

    async def complete(self, request: ChatRequest, model: str) -> str:
        """Non-streaming completion."""
        ...

    def stream(
        self, request: ChatRequest, model: str
    ) -> AsyncGenerator[str, None]:
        """Streaming text deltas."""
        ...
