"""Query + corpus embedder with a fake/real split and a user-scoped cache.

Two embedding call sites (DATA_INGESTION §3): the background corpus pass (sync
beat, uncached) and the inline hot query path (``embed_query``, Redis-cached).

* ``settings.EMBED_MODE == "fake"`` -> the deterministic ``FakeEmbedder`` (no
  network, reproducible cosine ordering offline).
* otherwise -> the Modal-hosted BGE service (``ModalBGEEmbedder``) when
  ``EMBEDDER_BASE_URL`` is set, else Gemini via ``llm_client.embed``.

The BGE query-instruction prefix is BGE-specific and OFF for Gemini: it is
applied only when ``settings.EMBED_QUERY_PREFIX`` is non-empty, so query and
corpus are embedded symmetrically by default. The per-user query-embedding cache
(``user:{user_id}:emb:{sha256(text)}|{model}``, 1h TTL) is load-bearing for
free-tier quota, and is scoped per user to prevent cross-user cache poisoning.
"""

import hashlib
import json

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from backend.config import settings
from backend.embeddings.base import EmbeddingProvider
from backend.embeddings.modal_bge import ModalBGEEmbedder
from backend.llm.client import llm_client
from backend.testing.fakes import FakeEmbedder

_EMB_TTL_SECONDS = 3600  # 1 hour


def _batched(items: list[str], size: int) -> list[list[str]]:
    size = max(1, size)
    return [items[i : i + size] for i in range(0, len(items), size)]


class Embedder:
    """Embed queries (cached) and corpus texts (uncached), fake or real."""

    def __init__(self) -> None:
        self._fake = FakeEmbedder(settings.EMBED_DIM)
        self._redis: aioredis.Redis | None = None
        self._provider: EmbeddingProvider | None = None

    # -- internals -------------------------------------------------------- #
    def _real_provider(self) -> EmbeddingProvider | None:
        if not settings.EMBEDDER_BASE_URL:
            return None
        if self._provider is None:
            self._provider = ModalBGEEmbedder()
        return self._provider

    def _redis_client(self) -> aioredis.Redis:
        if self._redis is None:
            # bytes in/out (decode_responses=False) — vectors are JSON blobs.
            self._redis = aioredis.from_url(
                settings.REDIS_URL, decode_responses=False
            )
        return self._redis

    def _apply_prefix(self, text: str) -> str:
        prefix = settings.EMBED_QUERY_PREFIX
        return f"{prefix}{text}" if prefix else text

    def _cache_key(self, user_id: str | None, text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        scope = user_id if user_id else "global"
        return f"user:{scope}:emb:{digest}|{settings.EMBED_MODEL}"

    async def _cache_get(self, key: str) -> list[float] | None:
        try:
            raw = await self._redis_client().get(key)
        except (RedisError, OSError):
            return None  # cache is best-effort; degrade to a fresh embed
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    async def _cache_set(self, key: str, vector: list[float]) -> None:
        try:
            await self._redis_client().set(
                key, json.dumps(vector), ex=_EMB_TTL_SECONDS
            )
        except (RedisError, OSError):
            pass  # never fail a query because the cache is unreachable

    # -- public API ------------------------------------------------------- #
    async def embed_query(
        self, text: str, user_id: str | None = None
    ) -> list[float]:
        """Embed a single query string, reading/writing the per-user cache.

        The cache key hashes the raw query text (per the DATA_INGESTION §3
        contract); the vector stored is the embedding of the prefix-applied
        text, so query and corpus stay in the same space.
        """
        key = self._cache_key(user_id, text)
        cached = await self._cache_get(key)
        if cached is not None:
            return cached
        instruction = settings.EMBEDDER_QUERY_INSTRUCTION or None
        vector = (
            await self.embed_texts([self._apply_prefix(text)], instruction=instruction)
        )[0]
        await self._cache_set(key, vector)
        return vector

    async def embed_texts(
        self, texts: list[str], instruction: str | None = None
    ) -> list[list[float]]:
        """Embed a batch of texts (corpus path — never cached).

        Fake mode returns deterministic offline vectors; real mode routes to the
        Modal BGE service (``ModalBGEEmbedder``) when ``EMBEDDER_BASE_URL`` is
        set, else falls back to Gemini via ``llm_client.embed``. ``instruction``
        is the BGE query-side retrieval instruction (``None`` for documents).
        """
        if not texts:
            return []
        if settings.EMBED_MODE == "fake":
            return await self._fake.embed_texts(texts)
        provider = self._real_provider()
        if provider is not None:
            return await provider.embed(texts, instruction=instruction)
        vectors: list[list[float]] = []
        for batch in _batched(texts, settings.GEMINI_EMBED_BATCH_SIZE):
            vectors.extend(await llm_client.embed(batch))
        return vectors


embedder = Embedder()
