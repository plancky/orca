"""Hermetic QA for the Modal BGE embedder adapter + Embedder routing.

No real Modal call: every request is served by an `httpx.MockTransport`, so this
proves the adapter's request shape (`POST /embed`, Modal-Key/Modal-Secret auth
headers, `input`/`instruction` body), 32-batch chunking, transient-503 retry,
and response parsing offline — mirroring the Gemini client's `test_llm_qa.py`.
It also verifies the `Embedder` real-mode routing (corpus -> no instruction,
query -> the configured retrieval instruction).
"""

import json

import httpx
import pytest

from backend.config import settings
from backend.embeddings.base import EmbeddingProvider
from backend.embeddings.embedder import Embedder
from backend.embeddings.modal_bge import ModalBGEEmbedder


def _install(monkeypatch, handler) -> ModalBGEEmbedder:
    monkeypatch.setattr(settings, "EMBEDDER_BASE_URL", "https://bge.modal.run")
    monkeypatch.setattr(settings, "MODAL_PROXY_TOKEN_ID", "tid")
    monkeypatch.setattr(settings, "MODAL_PROXY_TOKEN_SECRET", "tsec")
    real_cls = httpx.AsyncClient

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_cls(**kwargs)

    monkeypatch.setattr("backend.embeddings.modal_bge.httpx.AsyncClient", factory)
    return ModalBGEEmbedder()


def _embed_response(n: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "embeddings": [[float(i)] for i in range(n)],
            "model": "BAAI/bge-large-en",
            "dimensions": 1024,
            "count": n,
        },
    )


# --- HAPPY -----------------------------------------------------------------


async def test_embed_request_shape_and_auth_headers(monkeypatch):
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["modal_key"] = request.headers.get("modal-key")
        seen["modal_secret"] = request.headers.get("modal-secret")
        seen["body"] = json.loads(request.content)
        return _embed_response(1)

    adapter = _install(monkeypatch, handler)
    vectors = await adapter.embed(["Paris is the capital of France"])

    assert vectors == [[0.0]]
    assert seen["path"] == "/embed"
    assert seen["modal_key"] == "tid"
    assert seen["modal_secret"] == "tsec"
    assert seen["body"] == {
        "input": ["Paris is the capital of France"],
        "instruction": None,
    }
    await adapter.aclose()


async def test_embed_batches_at_32(monkeypatch):
    batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        batch_sizes.append(len(body["input"]))
        return _embed_response(len(body["input"]))

    adapter = _install(monkeypatch, handler)
    vectors = await adapter.embed([f"t{i}" for i in range(70)])

    assert len(vectors) == 70
    assert batch_sizes == [32, 32, 6]  # max 32 per the service contract
    await adapter.aclose()


async def test_instruction_is_forwarded(monkeypatch):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return _embed_response(1)

    adapter = _install(monkeypatch, handler)
    await adapter.embed(["q"], instruction="Represent this sentence:")

    assert bodies[0]["instruction"] == "Represent this sentence:"
    await adapter.aclose()


async def test_empty_input_short_circuits(monkeypatch):
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return _embed_response(0)

    adapter = _install(monkeypatch, handler)
    assert await adapter.embed([]) == []
    assert called["n"] == 0


async def test_503_is_retried_then_succeeds(monkeypatch):
    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("backend.embeddings.modal_bge.asyncio.sleep", _fake_sleep)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"embeddings": [[1.0]]})

    adapter = _install(monkeypatch, handler)
    vectors = await adapter.embed(["hi"])

    assert vectors == [[1.0]]
    assert attempts["n"] == 2
    assert len(sleeps) == 1
    await adapter.aclose()


# --- FAILURE / CONTRACT -----------------------------------------------------


async def test_missing_base_url_raises(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDER_BASE_URL", "")
    adapter = ModalBGEEmbedder()
    with pytest.raises(RuntimeError, match="EMBEDDER_BASE_URL required"):
        await adapter.embed(["hi"])


def test_adapter_conforms_to_port():
    assert issubclass(ModalBGEEmbedder, EmbeddingProvider)


# --- EMBEDDER ROUTING -------------------------------------------------------


async def test_real_mode_routes_corpus_to_bge_without_instruction(monkeypatch):
    monkeypatch.setattr(settings, "EMBED_MODE", "real")
    monkeypatch.setattr(settings, "EMBEDDER_BASE_URL", "https://bge.modal.run")
    calls: list[tuple[list[str], str | None]] = []

    async def fake_embed(self, texts, instruction=None):
        calls.append((list(texts), instruction))
        return [[0.1] for _ in texts]

    monkeypatch.setattr(ModalBGEEmbedder, "embed", fake_embed)
    out = await Embedder().embed_texts(["doc a", "doc b"])

    assert out == [[0.1], [0.1]]
    assert calls == [(["doc a", "doc b"], None)]


async def test_real_mode_routes_query_with_instruction(monkeypatch):
    monkeypatch.setattr(settings, "EMBED_MODE", "real")
    monkeypatch.setattr(settings, "EMBEDDER_BASE_URL", "https://bge.modal.run")
    monkeypatch.setattr(settings, "EMBEDDER_QUERY_INSTRUCTION", "Q-INSTR")
    monkeypatch.setattr(settings, "EMBED_QUERY_PREFIX", "")
    calls: list[tuple[list[str], str | None]] = []

    async def fake_embed(self, texts, instruction=None):
        calls.append((list(texts), instruction))
        return [[0.2] for _ in texts]

    monkeypatch.setattr(ModalBGEEmbedder, "embed", fake_embed)
    out = await Embedder().embed_query("find me", user_id="u1")

    assert out == [0.2]
    assert calls == [(["find me"], "Q-INSTR")]


async def test_fake_mode_ignores_bge(monkeypatch):
    monkeypatch.setattr(settings, "EMBED_MODE", "fake")
    monkeypatch.setattr(settings, "EMBEDDER_BASE_URL", "https://bge.modal.run")

    async def boom(self, texts, instruction=None):
        raise AssertionError("BGE must not be called in fake mode")

    monkeypatch.setattr(ModalBGEEmbedder, "embed", boom)
    out = await Embedder().embed_texts(["x"])

    assert len(out) == 1
    assert len(out[0]) == settings.EMBED_DIM
