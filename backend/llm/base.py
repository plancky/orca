"""Adapter-pattern port for chat inference + embeddings.

`LLMProvider` is the Target interface every inference call site depends on
(classifier, planner, synthesizer, deferred-arg extractor, JSON repair). Each
backend — the Gemini OpenAI-compat client and the Modal-hosted Qwen server — is
a concrete Adapter conforming to this contract, so the active provider is a
one-line factory swap with zero call-site changes.
"""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        response_format: str | None = None,
        temperature: float = 0,
    ) -> str: ...

    @abstractmethod
    async def embed(
        self, texts: list[str], dimensions: int | None = None
    ) -> list[list[float]]: ...

    async def aclose(self) -> None:
        return None
