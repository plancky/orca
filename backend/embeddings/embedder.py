class Embedder:
    async def embed_query(self, text: str, user_id: str | None = None) -> list[float]:
        raise NotImplementedError("Wave B3 fills this")

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("Wave B3 fills this")


embedder = Embedder()
