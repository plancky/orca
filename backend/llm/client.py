"""Async OpenAI-compatible inference client for Google Gemini (AI Studio).

Reaches Gemini ONLY through its OpenAI-compatibility REST layer via plain httpx
(no google-* SDK). `INFERENCE_BASE_URL` already ends in `/v1beta/openai/`, so the
endpoints are `{BASE}chat/completions` and `{BASE}embeddings` (no extra `/v1`).
Free-tier posture: bounded concurrency, per-request timeout, and exponential
backoff with jitter on transient 429/503 (honoring `Retry-After`).
"""

import asyncio
import random
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from backend.config import settings
from backend.llm.base import LLMProvider
from backend.llm.modal_qwen import ModalQwenAdapter

_RETRY_STATUS = frozenset({429, 503})
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 60.0
_REQUEST_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class LLMClient(LLMProvider):
    """Holds one shared `httpx.AsyncClient` for chat + embedding calls."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._semaphore: asyncio.Semaphore | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.INFERENCE_BASE_URL,
                timeout=_REQUEST_TIMEOUT,
            )
        return self._client

    def _get_semaphore(self) -> asyncio.Semaphore:
        # Created lazily inside a coroutine so it binds to the running loop.
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(settings.GEMINI_MAX_CONCURRENCY)
        return self._semaphore

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def chat(
        self,
        messages: list[dict],
        response_format: str | None = None,
        temperature: float = 0,
    ) -> str:
        payload: dict[str, Any] = {
            "model": settings.CHAT_MODEL,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        data = await self._post("chat/completions", payload)
        return data["choices"][0]["message"]["content"]

    async def embed(
        self, texts: list[str], dimensions: int | None = None
    ) -> list[list[float]]:
        dim = dimensions or settings.EMBED_DIM
        batch_size = max(1, settings.GEMINI_EMBED_BATCH_SIZE)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            data = await self._post(
                "embeddings",
                {"model": settings.EMBED_MODEL, "input": batch, "dimensions": dim},
            )
            items = sorted(data["data"], key=lambda item: item["index"])
            vectors.extend(item["embedding"] for item in items)
        return vectors

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        key = settings.GEMINI_STUDIO_API_KEY
        if not key:
            raise RuntimeError(
                "GEMINI_STUDIO_API_KEY required for live inference"
            )
        client = self._get_client()
        headers = {"Authorization": f"Bearer {key}"}
        async with self._get_semaphore():
            for attempt in range(settings.GEMINI_MAX_RETRIES + 1):
                response = await client.post(path, json=payload, headers=headers)
                retryable = response.status_code in _RETRY_STATUS
                if retryable and attempt < settings.GEMINI_MAX_RETRIES:
                    await asyncio.sleep(_backoff_delay(response, attempt))
                    continue
                response.raise_for_status()
                return response.json()
        raise RuntimeError("unreachable: retry loop exited without result")


def _backoff_delay(response: httpx.Response, attempt: int) -> float:
    """Exponential backoff + jitter; prefers a server `Retry-After` hint."""
    retry_after = _parse_retry_after(response.headers.get("Retry-After"))
    if retry_after is not None:
        return retry_after
    base = _BACKOFF_BASE_SECONDS * (2**attempt)
    return min(base + random.uniform(0, _BACKOFF_BASE_SECONDS), _BACKOFF_MAX_SECONDS)


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max((when - datetime.now(when.tzinfo)).total_seconds(), 0.0)


def _build_llm_client() -> LLMProvider:
    provider = settings.LLM_PROVIDER.strip().lower()
    if provider == "auto":
        provider = "modal_qwen" if settings.LLM_BASE_URL else "gemini"
    if provider in {"modal_qwen", "modal", "qwen"}:
        return ModalQwenAdapter()
    if provider in {"gemini", "google"}:
        return LLMClient()
    raise ValueError(
        f"Unknown LLM_PROVIDER={settings.LLM_PROVIDER!r} "
        "(expected auto|modal_qwen|gemini)"
    )


llm_client: LLMProvider = _build_llm_client()
