"""In-process retrieval eval harness: golden set -> Precision@5 + search latency.

Seeds the deterministic mock corpus, then for every golden query embeds it
(``Embedder``, EMBED_MODE-aware) -> runs ``hybrid_search`` over the query's
service(s) -> collapses to the top-5 datasource items -> computes **Precision@5**
against the expected item ids plus the **per-query search latency**. A clear
metrics table (per-query rows + mean P@5 + p50/p95/max latency) is printed.

Split acceptance (PLAN.md §Evaluation harness l.657-663, todo F1):

* **hermetic** (``EMBED_MODE=fake``, no key): the harness RUNS end-to-end, returns
  a metrics dict, and asserts **search latency < 500ms** (search is model-free —
  pgvector cosine over precomputed chunks). Precision@5 is COMPUTED and printed
  but NOT threshold-asserted, because fake vectors make relevance ordering
  arbitrary.
* **graded** (``EMBED_MODE=real``, Gemini key — the F5 live wave): additionally
  asserts **mean Precision@5 > 0.8**, honoring ``settings.RERANK_ENABLED``.

Seeding: ``seed_corpus`` always writes deterministic *fake* vectors (it is
fake-only by design), so a default ``run_eval()`` is fully self-contained and
hermetic (the seed is left uncommitted and rolls back on session close). The F5
real path re-embeds the corpus with Gemini via the sync worker FIRST, then calls
``run_eval(seed=False)`` to grade its query embeddings against that real corpus.
"""

import asyncio
import json
import math
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from backend.config import settings
from backend.db.models import Conversation, Task, TaskKind, TaskStatus, User
from backend.db.session import async_session_factory
from backend.embeddings.embedder import Embedder
from backend.embeddings.reranker import rerank
from backend.embeddings.search import hybrid_search
from backend.providers.mock.seed_corpus import SEED_EMAIL, seed_corpus
from backend.workers.orchestrate import pipeline

_GOLDEN_PATH = Path(__file__).with_name("golden_set.json")
_TOP_K = 5
_SEARCH_LATENCY_BUDGET_MS = 500.0
_MIN_MEAN_PRECISION = 0.8

# Business-id column returned by hybrid_search for each service's datasource row.
_ID_FIELD: dict[str, str] = {
    "gmail": "email_id",
    "gcal": "event_id",
    "gdrive": "file_id",
}


@dataclass(frozen=True, slots=True)
class _EvalContext:
    """The loop-invariant retrieval environment shared across golden queries."""

    session: AsyncSession
    embedder: Embedder
    user_id: str


def load_golden(path: Path = _GOLDEN_PATH) -> list[dict[str, Any]]:
    """Load the golden query set (the ``queries`` array) from JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["queries"]


def precision_at_k(
    retrieved_ids: list[str], expected_ids: set[str], k: int = _TOP_K
) -> float:
    """Fraction of the (up to ``k``) expected items surfaced in the top ``k``.

    The denominator is ``min(k, |expected|)`` rather than a bare ``k`` so a query
    with fewer than ``k`` relevant items can still reach 1.0 — a bare ``/k`` would
    cap a 2-relevant query at 0.4 and make the >0.8 graded gate unreachable.
    """
    if not expected_ids:
        return 0.0
    top = retrieved_ids[:k]
    hits = sum(1 for item_id in top if item_id in expected_ids)
    return hits / min(k, len(expected_ids))


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile (``pct`` in 0..100); 0.0 for empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


async def _evaluate_query(
    ctx: _EvalContext, entry: dict[str, Any]
) -> dict[str, Any]:
    """Embed one query, search its service(s), and score the top-5 collapsed items."""
    query = entry["query"]
    services = entry["services"]
    expected: set[str] = set(entry["expected"])

    embed_start = time.perf_counter()
    query_vec = await ctx.embedder.embed_query(query, user_id=ctx.user_id)
    embed_ms = (time.perf_counter() - embed_start) * 1000.0

    ranked: list[dict[str, Any]] = []
    search_ms = 0.0
    for service in services:
        search_start = time.perf_counter()
        items = await hybrid_search(
            ctx.session, query_vec, service, ctx.user_id, top_k=_TOP_K
        )
        search_ms += (time.perf_counter() - search_start) * 1000.0
        id_field = _ID_FIELD[service]
        for item in items:
            item_id = item.get(id_field)
            if item_id is not None:
                ranked.append(
                    {
                        "item_id": str(item_id),
                        "score": float(item["score"]),
                        "chunk_text": item.get("chunk_text", ""),
                    }
                )

    # Default ranking is the recency-decayed cosine score; the optional cross-
    # encoder rerank (settings.RERANK_ENABLED) reorders in place, else passthrough.
    ranked.sort(key=lambda it: it["score"], reverse=True)
    ranked = await rerank(query, ranked)
    retrieved_ids = [it["item_id"] for it in ranked[:_TOP_K]]

    return {
        "id": entry["id"],
        "category": entry["category"],
        "query": query,
        "services": services,
        "expected_ids": sorted(expected),
        "retrieved_ids": retrieved_ids,
        "hits": sum(1 for i in retrieved_ids if i in expected),
        "precision_at_5": precision_at_k(retrieved_ids, expected),
        "search_latency_ms": search_ms,
        "embed_latency_ms": embed_ms,
    }


async def _seeded_user_id(session: AsyncSession) -> str:
    """Resolve the existing seed user (F5 ``seed=False`` path: corpus pre-embedded)."""
    superuser = (
        await session.execute(
            select(User.id).where(col(User.is_superuser).is_(True))
        )
    ).scalars().first()
    if superuser is not None:
        return str(superuser)
    for email in (settings.FIRST_SUPERUSER_EMAIL, SEED_EMAIL):
        found = (
            await session.execute(select(User.id).where(User.email == email))
        ).scalars().first()
        if found is not None:
            return str(found)
    raise RuntimeError(
        "seed=False but no seeded user found — seed the corpus first "
        "(EMBED_MODE=real sync) or call run_eval() with seed=True"
    )


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    precisions = [r["precision_at_5"] for r in results]
    latencies = [r["search_latency_ms"] for r in results]
    return {
        "num_queries": len(results),
        "mean_precision_at_5": (sum(precisions) / len(precisions))
        if precisions
        else 0.0,
        "search_latency_p50_ms": _percentile(latencies, 50.0),
        "search_latency_p95_ms": _percentile(latencies, 95.0),
        "search_latency_max_ms": max(latencies) if latencies else 0.0,
        "embed_mode": settings.EMBED_MODE,
        "rerank_enabled": settings.RERANK_ENABLED,
        "per_query": results,
    }


def _print_report(metrics: dict[str, Any]) -> None:
    graded = metrics["embed_mode"] == "real"
    mode, rerank_on = metrics["embed_mode"], metrics["rerank_enabled"]
    rule = "=" * 94
    lines = [
        rule,
        "RETRIEVAL EVAL — golden-set Precision@5 + search latency",
        f"embed_mode={mode}  rerank_enabled={rerank_on}  "
        f"queries={metrics['num_queries']}",
        rule,
        f"{'id':<28}{'category':<9}{'services':<20}{'P@5':>6}{'hits':>6}{'search_ms':>11}",
        "-" * 94,
    ]
    for r in metrics["per_query"]:
        lines.append(
            f"{r['id']:<28}{r['category']:<9}{','.join(r['services']):<20}"
            f"{r['precision_at_5']:>6.2f}{r['hits']:>6}{r['search_latency_ms']:>11.2f}"
        )
    note = (
        "asserted > 0.8 (real embeddings)"
        if graded
        else "computed only — fake vectors, not threshold-asserted"
    )
    lines += [
        "-" * 94,
        f"mean Precision@5 : {metrics['mean_precision_at_5']:.3f}  ({note})",
        f"search latency   : p50={metrics['search_latency_p50_ms']:.2f}ms  "
        f"p95={metrics['search_latency_p95_ms']:.2f}ms  "
        f"max={metrics['search_latency_max_ms']:.2f}ms  "
        f"(asserted < {_SEARCH_LATENCY_BUDGET_MS:.0f}ms)",
        rule,
    ]
    print("\n".join(lines))


def _assert_thresholds(metrics: dict[str, Any]) -> None:
    max_search = metrics["search_latency_max_ms"]
    if max_search >= _SEARCH_LATENCY_BUDGET_MS:
        raise AssertionError(
            f"search latency {max_search:.1f}ms exceeds the "
            f"{_SEARCH_LATENCY_BUDGET_MS:.0f}ms budget"
        )
    # Precision@5 is a HARD gate only under real embeddings; fake vectors make
    # relevance ordering arbitrary so it is computed + printed but never asserted.
    if metrics["embed_mode"] == "real":
        mean_p = metrics["mean_precision_at_5"]
        if mean_p <= _MIN_MEAN_PRECISION:
            raise AssertionError(
                f"mean Precision@5 {mean_p:.3f} <= {_MIN_MEAN_PRECISION} gate"
            )


async def run_eval(
    golden: list[dict[str, Any]] | None = None, *, seed: bool = True
) -> dict[str, Any]:
    """Grade the golden set in-process and return the metrics dict.

    ``seed`` (default True) seeds the deterministic corpus in an uncommitted
    session that rolls back on close — self-contained and hermetic. Pass
    ``seed=False`` (F5) to grade against a corpus already persisted with real
    Gemini vectors. Always asserts search latency < 500ms; asserts mean
    Precision@5 > 0.8 only when ``EMBED_MODE=real``.
    """
    if golden is None:
        golden = load_golden()
    embedder = Embedder()
    results: list[dict[str, Any]] = []
    async with async_session_factory() as session:
        user_id = (
            str(await seed_corpus(session)) if seed else await _seeded_user_id(session)
        )
        ctx = _EvalContext(session=session, embedder=embedder, user_id=user_id)
        for entry in golden:
            results.append(await _evaluate_query(ctx, entry))
        # No commit: a seeded corpus is left uncommitted and rolls back here.

    metrics = _aggregate(results)
    _print_report(metrics)
    _assert_thresholds(metrics)

    if settings.EMBED_MODE == "real":
        print("\n[eval] Running end-to-end smoke test (pipeline coroutine)...")
        async with async_session_factory() as session:
            user_uuid = uuid.UUID(user_id)
            conv = Conversation(user_id=user_uuid, title="Eval Smoke Test")
            session.add(conv)
            await session.flush()
            task = Task(
                user_id=user_uuid,
                conversation_id=conv.id,
                kind=TaskKind.QUERY.value,
                status=TaskStatus.QUEUED.value,
            )
            session.add(task)
            await session.commit()
            
            task_id_str = str(task.id)
            conv_id_str = str(conv.id)
            
        await pipeline(
            task_id=task_id_str,
            user_id=str(user_uuid),
            query="Find emails from sarah@company.com about the budget",
            conversation_id=conv_id_str,
        )
        print("[eval] End-to-end smoke test passed.")

    return metrics


def main() -> None:
    asyncio.run(run_eval())


if __name__ == "__main__":
    main()
