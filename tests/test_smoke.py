import asyncio

import backend.main
from backend.features import conflict
from backend.orchestration.utils.tools import REGISTRY
from backend.testing.fakes import FakeEmbedder, _deterministic_vector


def test_import_main() -> None:
    assert backend.main.app is not None


def test_conflict_detect_registered() -> None:
    assert "conflict.detect" in REGISTRY
    assert REGISTRY["conflict.detect"] is conflict.detect_overlaps


def test_deterministic_vector_is_reproducible() -> None:
    v1 = _deterministic_vector("hello world")
    v2 = _deterministic_vector("hello world")
    v3 = _deterministic_vector("a different sentence")
    assert v1 == v2
    assert v1 != v3
    assert len(v1) == 1024
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-9


def test_fake_embedder_matches_and_batches() -> None:
    emb = FakeEmbedder()
    a = asyncio.run(emb.embed_query("hello world"))
    b = asyncio.run(emb.embed_query("hello world"))
    assert a == b == _deterministic_vector("hello world")
    vecs = asyncio.run(emb.embed_texts(["alpha", "beta"]))
    assert len(vecs) == 2
    assert vecs[0] != vecs[1]
