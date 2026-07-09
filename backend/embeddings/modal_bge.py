"""Modal-hosted BGE embedder adapter over the `/embed` HTTP API.

Adapts the BGE service (`POST /embed` with `{"input", "instruction"}` ->
`{"embeddings": [...]}`, 1024-dim L2-normalized) to the `EmbeddingProvider`
port via plain httpx. Auth reuses the chat LLM's Modal proxy-token pair as
`Modal-Key` / `Modal-Secret` headers. The service scales to zero, so the read
timeout is generous for cold starts and transient 503s are retried.
"""

import asyncio
import random
from typing import Any

import httpx

from backend.config import settings
from backend.embeddings.base import EmbeddingProvider

_MAX_BATCH = 32
_RETRY_STATUS = frozenset({503})
_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 0.5
# Cold start = GPU boot + model load (~30-40s per the service contract).
_REQUEST_TIMEOUT = httpx.Timeout(90.0, connect=10.0)


class ModalBGEEmbedder(EmbeddingProvider):
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            if not settings.EMBEDDER_BASE_URL:
                raise RuntimeError(
                    "EMBEDDER_BASE_URL required for the Modal BGE embedder"
                )
            self._client = httpx.AsyncClient(
                base_url=settings.EMBEDDER_BASE_URL,
                timeout=_REQUEST_TIMEOUT,
                headers={
                    "Modal-Key": settings.MODAL_PROXY_TOKEN_ID,
                    "Modal-Secret": settings.MODAL_PROXY_TOKEN_SECRET,
                },
            )
        return self._client

    async def embed(
        self, texts: list[str], instruction: str | None = None
    ) -> list[list[float]]:
        if not texts:
            return []
        client = self._get_client()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _MAX_BATCH):
            batch = texts[start : start + _MAX_BATCH]
            data = await self._post_embed(client, batch, instruction)
            vectors.extend(data["embeddings"])
        return vectors

    async def _post_embed(
        self, client: httpx.AsyncClient, batch: list[str], instruction: str | None
    ) -> dict[str, Any]:
        payload = {"input": batch, "instruction": instruction}
        for attempt in range(_MAX_RETRIES + 1):
            response = await client.post("/embed", json=payload)
            if response.status_code in _RETRY_STATUS and attempt < _MAX_RETRIES:
                await asyncio.sleep(
                    _BACKOFF_BASE_SECONDS * (2**attempt)
                    + random.uniform(0, _BACKOFF_BASE_SECONDS)
                )
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("unreachable: embed retry loop exited without result")

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
