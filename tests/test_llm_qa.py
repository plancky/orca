"""Hermetic QA for the Gemini OpenAI-compat LLM client + JSON parse/repair.

No real Gemini call: every request is served by an `httpx.MockTransport` handler
that returns canned OpenAI-compatible responses, so this suite proves request
shape (endpoint, Bearer auth, model, JSON mode), batching, transient-error
retry, and the parse→validate→repair→retry contract entirely offline.
"""

import json

import httpx
import pytest
from pydantic import BaseModel

from backend.config import settings
from backend.llm.client import LLMClient
from backend.llm.json_utils import JSONRepairError, extract_and_validate


class _Model(BaseModel):
    a: int


def _make_client(handler, monkeypatch) -> LLMClient:
    monkeypatch.setattr(settings, "GEMINI_STUDIO_API_KEY", "test-key")
    transport = httpx.MockTransport(handler)
    injected = httpx.AsyncClient(
        base_url=settings.INFERENCE_BASE_URL, transport=transport
    )
    return LLMClient(client=injected)


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


# --- HAPPY -----------------------------------------------------------------


async def test_chat_returns_content_with_correct_request_shape(monkeypatch):
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _chat_response('{"a": 1}')

    client = _make_client(handler, monkeypatch)
    content = await client.chat(
        [{"role": "user", "content": "hi"}], response_format="json_object"
    )

    assert content == '{"a": 1}'
    assert str(seen["path"]).endswith("/v1beta/openai/chat/completions")
    assert seen["auth"] == "Bearer test-key"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == settings.CHAT_MODEL
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    await client.aclose()


async def test_chat_omits_response_format_when_not_json_object(monkeypatch):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return _chat_response("plain text answer")

    client = _make_client(handler, monkeypatch)
    content = await client.chat([{"role": "user", "content": "hi"}])

    assert content == "plain text answer"
    assert "response_format" not in bodies[0]
    await client.aclose()


async def test_extract_and_validate_parses_fenced_json():
    model = await extract_and_validate('```json\n{"a": 1}\n```', _Model)
    assert model == _Model(a=1)


async def test_embed_batches_and_passes_dimensions(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_EMBED_BATCH_SIZE", 2)
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        data = [
            {"index": i, "embedding": [float(len(t))]}
            for i, t in enumerate(body["input"])
        ]
        return httpx.Response(200, json={"data": data})

    client = _make_client(handler, monkeypatch)
    vectors = await client.embed(["a", "bb", "ccc"], dimensions=8)

    assert vectors == [[1.0], [2.0], [3.0]]
    assert [c["input"] for c in calls] == [["a", "bb"], ["ccc"]]  # batched at 2
    assert calls[0]["dimensions"] == 8
    assert calls[0]["model"] == settings.EMBED_MODEL
    assert str(client._get_client().base_url).endswith("/v1beta/openai/")
    await client.aclose()


async def test_chat_retries_on_transient_errors_and_honors_retry_after(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_MAX_RETRIES", 3)
    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("backend.llm.client.asyncio.sleep", _fake_sleep)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={})
        if attempts["n"] == 2:
            return httpx.Response(503, json={})
        return _chat_response("ok")

    client = _make_client(handler, monkeypatch)
    content = await client.chat([{"role": "user", "content": "hi"}])

    assert content == "ok"
    assert attempts["n"] == 3  # 1 initial + 2 retries, then success
    assert sleeps[0] == 2.0  # server Retry-After honored
    await client.aclose()


# --- FAILURE ----------------------------------------------------------------


async def test_repair_path_one_reprompt_then_success(monkeypatch):
    reprompts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reprompts.append(json.loads(request.content))
        return _chat_response('{"a": 2}')

    client = _make_client(handler, monkeypatch)
    model = await extract_and_validate("not json at all", _Model, llm_client=client)

    assert model == _Model(a=2)
    assert len(reprompts) == 1  # EXACTLY one repair reprompt
    instruction = reprompts[0]["messages"][0]["content"]
    assert "_Model" in instruction
    assert reprompts[0]["response_format"] == {"type": "json_object"}
    await client.aclose()


async def test_repair_path_junk_twice_raises_json_repair_error(monkeypatch):
    reprompts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reprompts.append(json.loads(request.content))
        return _chat_response("still not json")

    client = _make_client(handler, monkeypatch)
    with pytest.raises(JSONRepairError):
        await extract_and_validate("junk", _Model, llm_client=client)

    assert len(reprompts) == 1  # one repair attempt, then give up
    await client.aclose()


async def test_repair_without_client_raises_json_repair_error():
    with pytest.raises(JSONRepairError):
        await extract_and_validate("not json", _Model)


async def test_missing_api_key_raises_runtime_error(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_STUDIO_API_KEY", "")
    client = LLMClient(
        client=httpx.AsyncClient(base_url=settings.INFERENCE_BASE_URL)
    )
    with pytest.raises(RuntimeError, match="GEMINI_STUDIO_API_KEY required"):
        await client.chat([{"role": "user", "content": "hi"}])
    await client.aclose()
