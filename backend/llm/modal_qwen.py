"""Modal-hosted Qwen adapter over the OpenAI-compatible Chat Completions API.

Adapts the `openai.AsyncOpenAI` SDK (the Adaptee) to the `LLMProvider` port so
classifier/planner/synth/repair reach a Modal deployment unchanged. Auth is
header-based (`Modal-Key` / `Modal-Secret`); the OpenAI `api_key` is a
required-but-unused placeholder. `openai` is imported lazily so the dependency
is only needed when this provider is actually selected.
"""

from typing import TYPE_CHECKING, Any

from backend.config import settings
from backend.llm.base import LLMProvider

if TYPE_CHECKING:
    from openai import AsyncOpenAI

_REQUEST_TIMEOUT = 60.0


class ModalQwenAdapter(LLMProvider):
    def __init__(self, client: "AsyncOpenAI | None" = None) -> None:
        self._client = client

    def _get_client(self) -> "AsyncOpenAI":
        if self._client is None:
            if not settings.LLM_BASE_URL:
                raise RuntimeError("LLM_BASE_URL required for the Modal Qwen provider")
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY or "unused",
                default_headers={
                    "Modal-Key": settings.MODAL_PROXY_TOKEN_ID,
                    "Modal-Secret": settings.MODAL_PROXY_TOKEN_SECRET,
                },
                max_retries=settings.GEMINI_MAX_RETRIES,
                timeout=_REQUEST_TIMEOUT,
            )
        return self._client

    async def chat(
        self,
        messages: list[dict],
        response_format: str | None = None,
        temperature: float = 0,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": settings.LLM_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": settings.LLM_MAX_TOKENS,
        }
        if response_format == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        effort = settings.LLM_REASONING_EFFORT.strip()
        if effort:
            kwargs["extra_body"] = {"reasoning_effort": effort}
        completion = await self._get_client().chat.completions.create(**kwargs)
        return completion.choices[0].message.content or ""

    async def embed(
        self, texts: list[str], dimensions: int | None = None
    ) -> list[list[float]]:
        if not texts:
            return []
        dim = dimensions or settings.EMBED_DIM
        batch_size = max(1, settings.GEMINI_EMBED_BATCH_SIZE)
        client = self._get_client()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            resp = await client.embeddings.create(
                model=settings.EMBED_MODEL, input=batch, dimensions=dim
            )
            items = sorted(resp.data, key=lambda item: item.index)
            vectors.extend(list(item.embedding) for item in items)
        return vectors

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
