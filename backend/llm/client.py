class LLMClient:
    async def chat(
        self,
        messages: list[dict],
        response_format: str | None = None,
        temperature: float = 0,
    ) -> str:
        raise NotImplementedError("Wave B2 fills this")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("Wave B2 fills this")


llm_client = LLMClient()
