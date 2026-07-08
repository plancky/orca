async def hybrid_search(
    session,
    query_embedding: list[float],
    service: str,
    user_id: str,
    filters: dict | None = None,
    top_k: int = 10,
) -> list[dict]:
    raise NotImplementedError("Wave B3 fills this")
