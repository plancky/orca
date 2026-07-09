"""Adapter-pattern port for text embedding.

`EmbeddingProvider` is the Target interface the `Embedder` orchestrator depends
on for real (non-fake) embedding. The Modal-hosted BGE service is a concrete
Adapter conforming to this contract, so swapping embedding backends is a
config-driven choice with no change to the search/sync/query call sites.
"""

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(
        self, texts: list[str], instruction: str | None = None
    ) -> list[list[float]]: ...

    async def aclose(self) -> None:
        return None
