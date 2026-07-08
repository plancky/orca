"""JSON-mode robustness: extract → validate → one repair reprompt → retry.

Gemini's OpenAI-compat JSON mode is not constrained decoding, so model output
may arrive fenced or with stray prose. We strip code fences, take the outermost
`{...}` object, and `model_validate`. On failure we send exactly ONE repair
reprompt through the provided chat client and retry the parse once; if it is
still invalid we raise the typed `JSONRepairError` so the executor can degrade.
"""

import json
import re
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\s*(.*?)\s*```", re.DOTALL)


class JSONRepairError(Exception):
    """Text could not be parsed into the target schema, even after one repair."""


class _ExtractionError(ValueError):
    """Internal: no JSON object could be located in the text."""


class _ChatClient(Protocol):
    async def chat(
        self,
        messages: list[dict],
        response_format: str | None = ...,
        temperature: float = ...,
    ) -> str: ...


def _extract_object(text: str) -> str:
    candidate = text.strip()
    fence = _FENCE_RE.search(candidate)
    if fence is not None:
        candidate = fence.group(1).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise _ExtractionError("no JSON object found in text")
    return candidate[start : end + 1]


def _parse(text: str, model_cls: type[T]) -> T:
    payload = json.loads(_extract_object(text))
    return model_cls.model_validate(payload)


def _repair_instruction(schema_name: str, text: str) -> str:
    return (
        f"Your last output was invalid JSON for schema {schema_name}. "
        "Return ONLY valid JSON, with no prose and no code fences.\n\n"
        f"Last output:\n{text}"
    )


async def extract_and_validate(
    text: str,
    model_cls: type[T],
    llm_client: _ChatClient | None = None,
    schema_name: str | None = None,
) -> T:
    name = schema_name or model_cls.__name__
    try:
        return _parse(text, model_cls)
    except (_ExtractionError, json.JSONDecodeError, ValidationError) as first_error:
        if llm_client is None:
            raise JSONRepairError(
                f"invalid JSON for {name} and no llm_client provided for repair"
            ) from first_error
    repaired = await llm_client.chat(
        [{"role": "user", "content": _repair_instruction(name, text)}],
        response_format="json_object",
    )
    try:
        return _parse(repaired, model_cls)
    except (_ExtractionError, json.JSONDecodeError, ValidationError) as exc:
        raise JSONRepairError(
            f"invalid JSON for {name} after one repair reprompt"
        ) from exc
