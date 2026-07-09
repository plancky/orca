"""Wave B3 QA — hybrid search over a live compose DB + FakeEmbedder.

Seeds a real user with 5 ``gmail_vector_store`` chunks spread across 3
``gmail_datasource`` rows (all vectors from the deterministic ``FakeEmbedder``,
no model server), then asserts:

* HAPPY — a query returns <= 3 items (chunks collapse to their datasource
  parent), the order is deterministic across runs, and every item belongs to the
  querying user.
* FAILURE — the same query as a *different* user returns 0 items (user scope
  isolation).

Every seeded row is removed in the fixture teardown. Vector rows are inserted
with ``text()`` + a raw-list param (the binding pgvector's asyncpg codec accepts;
see ``backend.embeddings.search``), while user/datasource rows use the ORM so
their Python-side defaults (ids, timestamps) are filled.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from backend.db.models import GmailDatasource, User
from backend.db.session import async_session_factory
from backend.embeddings.search import filter_search, hybrid_search
from backend.testing.fakes import FakeEmbedder

# All datasources share one received_at so recency decay is uniform and cannot
# perturb the cosine-driven order (keeps the determinism assertion honest).
_FIXED_RECEIVED = datetime(2024, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

# 5 chunks over 3 datasources: ds0 owns 3 chunks, ds1 and ds2 own 1 each — so a
# correct parent-collapse yields exactly 3 items, never 5.
_CHUNK_TEXTS = (
    "budget alpha",
    "budget beta",
    "budget gamma",
    "quarterly numbers",
    "team sync notes",
)
_CHUNK_TO_DS = (0, 0, 0, 1, 2)


async def _embed(chunk_text: str) -> list[float]:
    return (await FakeEmbedder().embed_texts([chunk_text]))[0]


async def _insert_chunk(
    session,
    ds_id: uuid.UUID,
    user_id: uuid.UUID,
    index: int,
    chunk_text: str,
) -> None:
    await session.execute(
        text(
            "INSERT INTO gmail_vector_store "
            "(id, datasource_id, user_id, thread_id, chunk_index, chunk_text, "
            " token_count, embedding) "
            "VALUES (:id, :d, :u, :th, :ci, :ct, :tc, :emb)"
        ),
        {
            "id": uuid.uuid4(),
            "d": ds_id,
            "u": user_id,
            "th": None,
            "ci": index,
            "ct": chunk_text,
            "tc": len(chunk_text) // 4,
            "emb": await _embed(chunk_text),
        },
    )


@pytest.fixture
async def seeded_gmail():
    """Yield ``(session, owner_id, other_id)`` with the owner's corpus seeded."""
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    async with async_session_factory() as session:
        try:
            session.add(
                User(id=owner_id, email=f"{owner_id}@qa.test", hashed_password="x")
            )
            session.add(
                User(id=other_id, email=f"{other_id}@qa.test", hashed_password="x")
            )
            await session.flush()  # no ORM relationship -> insert users first

            ds_ids: list[uuid.UUID] = []
            for i in range(3):
                ds = GmailDatasource(
                    user_id=owner_id,
                    email_id=f"e{i}",
                    subject=f"sub{i}",
                    content=f"budget report {i}",
                    received_at=_FIXED_RECEIVED,
                )
                session.add(ds)
                await session.flush()
                ds_ids.append(ds.id)

            for index, (chunk_text, ds_index) in enumerate(
                zip(_CHUNK_TEXTS, _CHUNK_TO_DS)
            ):
                await _insert_chunk(
                    session, ds_ids[ds_index], owner_id, index, chunk_text
                )
            await session.commit()

            yield session, owner_id, other_id
        finally:
            both = [owner_id, other_id]
            await session.execute(
                text("DELETE FROM gmail_vector_store WHERE user_id = ANY(:u)"),
                {"u": both},
            )
            await session.execute(
                text("DELETE FROM gmail_datasource WHERE user_id = ANY(:u)"),
                {"u": both},
            )
            await session.execute(
                text('DELETE FROM "user" WHERE id = ANY(:u)'), {"u": both}
            )
            await session.commit()


async def test_hybrid_search_collapses_to_parents_for_the_owner(seeded_gmail):
    # Given: an owner with 5 chunks across 3 gmail datasources.
    session, owner_id, _ = seeded_gmail
    query = await _embed("budget")

    # When: the owner searches.
    results = await hybrid_search(session, query, "gmail", str(owner_id))

    # Then: chunks collapse to <= 3 datasource-level items, one row per parent.
    assert 0 < len(results) <= 3
    datasource_ids = [item["datasource_id"] for item in results]
    assert len(datasource_ids) == len(set(datasource_ids))
    # And: every item is scoped to the querying user.
    assert all(item["user_id"] == owner_id for item in results)
    # And: every item carries a score.
    assert all("score" in item for item in results)


async def test_hybrid_search_order_is_deterministic_across_runs(seeded_gmail):
    # Given: the owner's seeded corpus and a fixed query vector.
    session, owner_id, _ = seeded_gmail
    query = await _embed("budget")

    # When: the same search runs twice.
    first = await hybrid_search(session, query, "gmail", str(owner_id))
    second = await hybrid_search(session, query, "gmail", str(owner_id))

    # Then: the datasource ordering is identical (deterministic FakeEmbedder).
    assert [i["datasource_id"] for i in first] == [
        i["datasource_id"] for i in second
    ]


async def test_hybrid_search_isolates_by_user(seeded_gmail):
    # Given: a different user who owns none of the seeded chunks.
    session, _, other_id = seeded_gmail
    query = await _embed("budget")

    # When: that user runs the same query.
    results = await hybrid_search(session, query, "gmail", str(other_id))

    # Then: none of the owner's items leak — user isolation holds.
    assert results == []


async def test_filter_search_returns_parents_without_embedding(seeded_gmail):
    # Given: an owner whose 3 datasources were all received on 2024-03-01.
    session, owner_id, _ = seeded_gmail
    window = {
        "received_at": {
            "start": datetime(2024, 2, 1, tzinfo=timezone.utc).isoformat(),
            "end": datetime(2024, 4, 1, tzinfo=timezone.utc).isoformat(),
        }
    }

    # When: a filter-only search runs (ISO-string range, no query embedding).
    results = await filter_search(session, "gmail", str(owner_id), filters=window)

    # Then: one row per owned datasource, user-scoped, with no cosine ranking.
    assert len(results) == 3
    ids = [item["datasource_id"] for item in results]
    assert len(ids) == len(set(ids))
    assert all(item["user_id"] == owner_id for item in results)
    assert all(item["distance"] is None for item in results)
    assert all(item["similarity"] is None for item in results)
    assert all("score" in item for item in results)


async def test_filter_search_honors_the_date_window(seeded_gmail):
    # Given: every seeded datasource sits on 2024-03-01.
    session, owner_id, _ = seeded_gmail
    window = {
        "received_at": {
            "start": datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat(),
            "end": datetime(2025, 2, 1, tzinfo=timezone.utc).isoformat(),
        }
    }

    # When: the window excludes all of them.
    results = await filter_search(session, "gmail", str(owner_id), filters=window)

    # Then: nothing matches.
    assert results == []


async def test_filter_search_isolates_by_user(seeded_gmail):
    # Given: a different user owns none of the seeded rows.
    session, _, other_id = seeded_gmail

    # When: they run an unfiltered filter search.
    results = await filter_search(session, "gmail", str(other_id))

    # Then: user scope holds — no leakage.
    assert results == []
