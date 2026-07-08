"""Optional cross-encoder rerank stage (disabled by default).

Hybrid cosine search is the default ranking signal; reranking is built but off
(``settings.RERANK_ENABLED`` default False, locked decision). Gemini exposes no
rerank endpoint, so enabling it would require a separate ``bge-reranker-v2-m3``
service — that slots in here. Until then this is a faithful passthrough that
preserves the incoming order.
"""

from backend.config import settings


async def rerank(query: str, items: list[dict]) -> list[dict]:
    """Return ``items`` reordered by relevance to ``query``.

    Passthrough when ``settings.RERANK_ENABLED`` is False (the default). When a
    cross-encoder service is later wired, its scoring replaces this branch.
    """
    if not settings.RERANK_ENABLED:
        return items
    # No cross-encoder backend is configured (Gemini has none); a bge-reranker
    # service would score (query, item.chunk_text) pairs and re-sort here.
    return items
