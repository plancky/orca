"""Wave F1 QA — the in-process retrieval eval harness.

Hermetic (``EMBED_MODE=fake`` -> ``FakeEmbedder``, no key). Asserts that:

* ``run_eval()`` returns a well-formed metrics dict — per-query rows plus the
  aggregate mean Precision@5 and p50/p95 search latencies — and that the
  model-free search stays under the asserted 500ms budget.
* a deliberately-wrong expected id **strictly lowers** that query's Precision@5,
  proving the metric measures relevance rather than returning a constant.
* ``precision_at_k`` normalizes by ``min(k, |expected|)`` so small relevant sets
  can still reach 1.0.
* the ``seed=False`` path grades against an already-committed corpus (the F5 real
  flow, which re-embeds + persists the corpus before grading).

Precision@5 values themselves are NOT asserted against a threshold here: fake
vectors make relevance ordering arbitrary (that hard gate is the real-key F5
wave). These tests pin the harness plumbing, not retrieval quality.
"""

import pytest

from backend.db.session import async_session_factory
from backend.eval.evaluate import load_golden, precision_at_k, run_eval
from backend.providers.mock.seed_corpus import seed_corpus


async def test_run_eval_returns_well_formed_metrics():
    # Given: the committed golden set over the deterministic mock corpus.
    golden = load_golden()

    # When: the harness grades it in-process (seeds its own corpus, hermetic).
    metrics = await run_eval()

    # Then: the summary carries a per-query breakdown plus aggregate metrics.
    assert metrics["num_queries"] == len(golden)
    assert len(metrics["per_query"]) == len(golden)
    assert 0.0 <= metrics["mean_precision_at_5"] <= 1.0
    assert metrics["search_latency_p50_ms"] >= 0.0
    assert metrics["search_latency_p95_ms"] >= metrics["search_latency_p50_ms"]
    # And: search is model-free, so the asserted <500ms budget holds hermetically.
    assert metrics["search_latency_max_ms"] < 500.0

    # And: every per-query row exposes the graded fields the report is built from.
    for row in metrics["per_query"]:
        assert 0.0 <= row["precision_at_5"] <= 1.0
        assert row["search_latency_ms"] >= 0.0
        assert set(row["expected_ids"])  # a non-empty expected set
        assert len(row["retrieved_ids"]) <= 5


async def test_wrong_expected_id_strictly_lowers_precision_at_5():
    # Given: a baseline run whose retrieval is deterministic (FakeEmbedder), so the
    # top retrieved id for the first query is a fixed, known value.
    baseline = await run_eval()
    probe = baseline["per_query"][0]
    assert probe["retrieved_ids"], "probe query retrieved nothing to grade against"
    top_id = probe["retrieved_ids"][0]

    correct = {
        "id": probe["id"],
        "category": probe["category"],
        "query": probe["query"],
        "services": probe["services"],
        "expected": [top_id],
    }
    wrong = {**correct, "expected": ["__definitely-not-a-real-item-id__"]}

    # When: the same query is graded against the right id vs a wrong id.
    correct_metrics = await run_eval(golden=[correct])
    wrong_metrics = await run_eval(golden=[wrong])

    # Then: the right id scores a perfect P@5 and the wrong id scores zero — the
    # wrong copy strictly lowers Precision@5, proving the metric measures.
    correct_p = correct_metrics["per_query"][0]["precision_at_5"]
    wrong_p = wrong_metrics["per_query"][0]["precision_at_5"]
    assert correct_p == pytest.approx(1.0)
    assert wrong_p == pytest.approx(0.0)
    assert wrong_p < correct_p


def test_precision_at_k_normalizes_by_relevant_count():
    # Given/When/Then: 2 relevant ids both in the top-5 -> denominator min(k,2)=2,
    # a perfect 1.0 (a bare /5 would wrongly cap this at 0.4).
    assert precision_at_k(["a", "b", "c"], {"a", "b"}) == pytest.approx(1.0)
    # And: a partial hit is the fraction of the (capped) relevant set surfaced.
    assert precision_at_k(["a", "x"], {"a", "b"}) == pytest.approx(0.5)
    # And: an empty expected set is 0.0 (no ZeroDivision).
    assert precision_at_k(["a"], set()) == pytest.approx(0.0)


async def test_seed_false_grades_against_a_preexisting_corpus():
    # Given: a corpus committed to the DB — the F5 real path re-embeds + persists
    # the corpus, then grades against it without re-seeding.
    async with async_session_factory() as session:
        await seed_corpus(session)
        await session.commit()
    entry = {
        "id": "probe-seed-false",
        "category": "single",
        "query": "emails from sarah about the budget",
        "services": ["gmail"],
        "expected": ["sarah-budget-001"],
    }

    # When: run_eval grades a single query without re-seeding (seed=False).
    metrics = await run_eval(golden=[entry], seed=False)

    # Then: it resolves the persisted seed user and returns graded metrics.
    assert metrics["num_queries"] == 1
    assert 0.0 <= metrics["per_query"][0]["precision_at_5"] <= 1.0
    assert metrics["search_latency_max_ms"] < 500.0
