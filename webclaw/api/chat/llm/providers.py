"""Concrete LLM provider clients (OpenAI, Anthropic, Gemini)."""
from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

import httpx

from .config import LLMSettings
from .types import ChatRequest, ProviderName

log = logging.getLogger("webclaw.chat.llm")


class ProviderError(RuntimeError):
    """Raised when a provider call fails in a failover-worthy way."""

    def __init__(self, provider: str, message: str, status_code: int | None = None):
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"{provider}: {message}")


def _is_failover_status(status: int) -> bool:
    return status in (408, 429, 500, 502, 503, 504)


class OpenAICompatibleProvider:
    """OpenAI chat completions (SSE streaming)."""

    def __init__(
        self,
        name: ProviderName,
        base_url: str,
        api_key: str,
        *,
        require_key: bool = True,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.require_key = require_key

    def available(self) -> bool:
        if not self.base_url:
            return False
        if self.require_key and not self.api_key:
            return False
        return True

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    async def complete(self, request: ChatRequest, model: str) -> str:
        body = {
            "model": model,
            "messages": _openai_messages(request),
            "stream": False,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    self._endpoint(), headers=self._headers(), json=body
                )
        except httpx.HTTPError as e:
            raise ProviderError(self.name.value, f"network error: {e}") from e

        if resp.status_code != 200:
            if _is_failover_status(resp.status_code):
                raise ProviderError(
                    self.name.value,
                    f"HTTP {resp.status_code}: {resp.text[:300]}",
                    resp.status_code,
                )
            raise ProviderError(
                self.name.value,
                f"HTTP {resp.status_code}: {resp.text[:300]}",
                resp.status_code,
            )

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content") or ""

    async def stream(
        self, request: ChatRequest, model: str
    ) -> AsyncGenerator[str, None]:
        body = {
            "model": model,
            "messages": _openai_messages(request),
            "stream": True,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST", self._endpoint(), headers=self._headers(), json=body
                ) as resp:
                    if resp.status_code != 200:
                        err = (await resp.aread()).decode("utf-8", errors="replace")[
                            :400
                        ]
                        raise ProviderError(
                            self.name.value,
                            f"HTTP {resp.status_code}: {err}",
                            resp.status_code,
                        )
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if event.get("error"):
                            msg = event["error"].get("message", "Unknown error")
                            raise ProviderError(self.name.value, msg)
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        text = delta.get("content") or ""
                        if text:
                            yield text
                        if choices[0].get("finish_reason"):
                            break
        except ProviderError:
            raise
        except httpx.HTTPError as e:
            raise ProviderError(self.name.value, f"network error: {e}") from e


class AnthropicProvider:
    """Anthropic Messages API (SSE)."""

    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com"):
        self.name = ProviderName.ANTHROPIC
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def available(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _body(self, request: ChatRequest, model: str, stream: bool) -> dict:
        return {
            "model": model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "system": request.system,
            "messages": [
                {"role": m["role"], "content": m["content"]}
                for m in request.messages
                if m.get("role") in ("user", "assistant")
            ],
            "stream": stream,
        }

    async def complete(self, request: ChatRequest, model: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/messages",
                    headers=self._headers(),
                    json=self._body(request, model, stream=False),
                )
        except httpx.HTTPError as e:
            raise ProviderError(self.name.value, f"network error: {e}") from e

        if resp.status_code != 200:
            raise ProviderError(
                self.name.value,
                f"HTTP {resp.status_code}: {resp.text[:300]}",
                resp.status_code,
            )
        data = resp.json()
        chunks = data.get("content") or []
        texts = [c.get("text", "") for c in chunks if c.get("type") == "text"]
        return "".join(texts)

    async def stream(
        self, request: ChatRequest, model: str
    ) -> AsyncGenerator[str, None]:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/v1/messages",
                    headers=self._headers(),
                    json=self._body(request, model, stream=True),
                ) as resp:
                    if resp.status_code != 200:
                        err = (await resp.aread()).decode("utf-8", errors="replace")[
                            :400
                        ]
                        raise ProviderError(
                            self.name.value,
                            f"HTTP {resp.status_code}: {err}",
                            resp.status_code,
                        )
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if not data_str:
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        etype = event.get("type")
                        if etype == "content_block_delta":
                            delta = event.get("delta") or {}
                            text = delta.get("text") or ""
                            if text:
                                yield text
                        elif etype == "error":
                            err = event.get("error") or {}
                            raise ProviderError(
                                self.name.value,
                                err.get("message", "Anthropic error"),
                            )
                        elif etype == "message_stop":
                            break
        except ProviderError:
            raise
        except httpx.HTTPError as e:
            raise ProviderError(self.name.value, f"network error: {e}") from e


class GeminiProvider:
    """Google Gemini generateContent API (SSE streaming)."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ):
        self.name = ProviderName.GEMINI
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def available(self) -> bool:
        return bool(self.api_key)

    def _body(self, request: ChatRequest) -> dict:
        contents: list[dict] = []
        for msg in request.messages:
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            contents.append(
                {
                    "role": "model" if role == "assistant" else "user",
                    "parts": [{"text": msg.get("content") or ""}],
                }
            )
        body: dict = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": request.max_tokens,
                "temperature": request.temperature,
            },
        }
        if request.system:
            body["systemInstruction"] = {"parts": [{"text": request.system}]}
        return body

    def _url(self, model: str, stream: bool) -> str:
        action = "streamGenerateContent" if stream else "generateContent"
        url = f"{self.base_url}/models/{model}:{action}?key={self.api_key}"
        if stream:
            url += "&alt=sse"
        return url

    async def complete(self, request: ChatRequest, model: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    self._url(model, stream=False),
                    headers={"Content-Type": "application/json"},
                    json=self._body(request),
                )
        except httpx.HTTPError as e:
            raise ProviderError(self.name.value, f"network error: {e}") from e

        if resp.status_code != 200:
            raise ProviderError(
                self.name.value,
                f"HTTP {resp.status_code}: {resp.text[:300]}",
                resp.status_code,
            )
        data = resp.json()
        return _gemini_text_from_response(data)

    async def stream(
        self, request: ChatRequest, model: str
    ) -> AsyncGenerator[str, None]:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    self._url(model, stream=True),
                    headers={"Content-Type": "application/json"},
                    json=self._body(request),
                ) as resp:
                    if resp.status_code != 200:
                        err = (await resp.aread()).decode("utf-8", errors="replace")[
                            :400
                        ]
                        raise ProviderError(
                            self.name.value,
                            f"HTTP {resp.status_code}: {err}",
                            resp.status_code,
                        )
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if not data_str:
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if event.get("error"):
                            err = event["error"]
                            msg = (
                                err.get("message")
                                if isinstance(err, dict)
                                else str(err)
                            )
                            raise ProviderError(self.name.value, msg or "Gemini error")
                        text = _gemini_text_from_response(event)
                        if text:
                            yield text
        except ProviderError:
            raise
        except httpx.HTTPError as e:
            raise ProviderError(self.name.value, f"network error: {e}") from e


def _gemini_text_from_response(data: dict) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


def _openai_messages(request: ChatRequest) -> list[dict[str, str]]:
    return [{"role": "system", "content": request.system}, *request.messages]


def build_providers(settings: LLMSettings) -> dict[ProviderName, object]:
    """Instantiate configured providers (unavailable ones are skipped by router)."""
    return {
        ProviderName.OPENAI: OpenAICompatibleProvider(
            ProviderName.OPENAI,
            settings.openai_base_url,
            settings.openai_api_key,
            require_key=True,
        ),
        ProviderName.ANTHROPIC: AnthropicProvider(settings.anthropic_api_key),
        ProviderName.GEMINI: GeminiProvider(
            settings.gemini_api_key,
            settings.gemini_base_url,
        ),
    }
