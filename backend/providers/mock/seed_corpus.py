"""Seed the deterministic mock corpus for ONE user.

``seed_corpus(session)`` is idempotent on the ``UNIQUE(user_id, item_id)`` of each
``*_datasource`` (re-seeding updates rows + replaces their chunks) and does NOT
commit — the caller (``scripts/seed.py`` or the QA harness) owns the transaction.
It attaches the corpus to the first superuser if one exists (B1's
``seed_superuser`` runs first in ``scripts/seed.py``), otherwise to a dedicated
``seeduser@example.com``.

Chunking goes through ``backend.embeddings.chunkers`` (B3). While B3 is still a
raising stub (parallel wave), each item degrades to a single whole-text chunk so
the corpus is fully populated + searchable now; the real chunker is picked up
automatically once B3 lands. Vectors are always ``FakeEmbedder`` (offline,
deterministic) — the mock plane never spends model quota.
"""

import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, col, select

from backend.config import settings
from backend.db.models import (
    GCalChunk,
    GCalDatasource,
    GDriveChunk,
    GDriveDatasource,
    GmailChunk,
    GmailDatasource,
    User,
)
from backend.embeddings.chunkers import chunk_gcal, chunk_gdrive, chunk_gmail
from backend.providers.mock._corpus_data import build_corpus
from backend.testing.fakes import FakeEmbedder

SEED_EMAIL = "seeduser@example.com"
# Non-functional hash: the seed user owns a corpus, it is not a login account.
# Kept decoupled from B1's core.security on purpose (parallel wave).
_PLACEHOLDER_HASH = "!seed-corpus-no-login"


def _chunk_or_fallback(
    fn: Callable[..., list[str]], args: tuple, fallback: str
) -> list[str]:
    """Real chunker output, or a single whole-text chunk if B3 is still a stub."""
    try:
        result = fn(*args)
    except NotImplementedError:
        result = []
    return result or [fallback]


async def _resolve_seed_user(session: AsyncSession) -> uuid.UUID:
    superuser = (
        await session.execute(select(User).where(col(User.is_superuser).is_(True)))
    ).scalars().first()
    if superuser is not None:
        return superuser.id
    for email in (settings.FIRST_SUPERUSER_EMAIL, SEED_EMAIL):
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalars().first()
        if existing is not None:
            return existing.id
    user = User(
        email=SEED_EMAIL,
        full_name="Seed User",
        hashed_password=_PLACEHOLDER_HASH,
        is_active=True,
        is_superuser=False,
        timezone=settings.DEFAULT_TZ,
    )
    session.add(user)
    await session.flush()
    return user.id


async def _upsert_datasource(
    session: AsyncSession,
    model: type[SQLModel],
    key_field: str,
    user_id: uuid.UUID,
    item: dict,
) -> SQLModel:
    existing = (
        await session.execute(
            select(model).where(
                col(getattr(model, "user_id")) == user_id,
                col(getattr(model, key_field)) == item[key_field],
            )
        )
    ).scalars().first()
    if existing is None:
        row = model(user_id=user_id, **item)
        session.add(row)
    else:
        for field, value in item.items():
            setattr(existing, field, value)
        row = existing
    await session.flush()
    return row


async def _replace_chunks(
    session: AsyncSession,
    chunk_model: type[SQLModel],
    datasource_id: uuid.UUID,
    user_id: uuid.UUID,
    texts: Sequence[str],
    vectors: Sequence[list[float]],
    thread_id: str | None = None,
) -> None:
    # Written via text() not the ORM: the frozen Wave-0 setup registers pgvector's
    # asyncpg binary codec AND the model's VECTOR type stringifies on bind — the two
    # collide on an ORM insert, so the embedding is bound as a raw list that the
    # codec encodes. Table name is a frozen __tablename__ constant (no injection).
    table = getattr(chunk_model, "__tablename__")
    await session.execute(
        text(f"DELETE FROM {table} WHERE datasource_id = :datasource_id"),
        {"datasource_id": datasource_id},
    )
    include_thread = thread_id is not None and hasattr(chunk_model, "thread_id")
    columns = [
        "id", "datasource_id", "user_id", "chunk_index",
        "chunk_text", "token_count", "embedding",
    ]
    if include_thread:
        columns.insert(4, "thread_id")
    stmt = text(
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(f':{c}' for c in columns)})"
    )
    for index, (chunk_text, vector) in enumerate(zip(texts, vectors, strict=True)):
        params: dict = {
            "id": uuid.uuid4(),
            "datasource_id": datasource_id,
            "user_id": user_id,
            "chunk_index": index,
            "chunk_text": chunk_text,
            "token_count": len(chunk_text.split()),
            "embedding": vector,
        }
        if include_thread:
            params["thread_id"] = thread_id
        await session.execute(stmt, params)


async def seed_corpus(session: AsyncSession) -> uuid.UUID:
    """Populate ``*_datasource`` + ``*_vector_store`` for the seed user."""
    user_id = await _resolve_seed_user(session)
    embedder = FakeEmbedder()
    corpus = build_corpus(datetime.now(timezone.utc))

    for item in corpus["gmail"]:
        ds = await _upsert_datasource(
            session, GmailDatasource, "email_id", user_id, item
        )
        texts = _chunk_or_fallback(
            chunk_gmail,
            (item.get("subject") or "", item["content"]),
            f"{item.get('subject') or ''}\n{item['content']}",
        )
        vectors = await embedder.embed_texts(texts)
        await _replace_chunks(
            session, GmailChunk, ds.id, user_id, texts, vectors,
            thread_id=item.get("thread_id"),
        )

    for item in corpus["gcal"]:
        ds = await _upsert_datasource(
            session, GCalDatasource, "event_id", user_id, item
        )
        title = item.get("title") or ""
        texts = _chunk_or_fallback(
            chunk_gcal,
            (title, item.get("description") or "", item.get("location") or ""),
            f"{title}\n{item.get('description') or ''}\n{item.get('location') or ''}",
        )
        vectors = await embedder.embed_texts(texts)
        await _replace_chunks(session, GCalChunk, ds.id, user_id, texts, vectors)

    for item in corpus["gdrive"]:
        ds = await _upsert_datasource(
            session, GDriveDatasource, "file_id", user_id, item
        )
        texts = _chunk_or_fallback(chunk_gdrive, (item["content"],), item["content"])
        vectors = await embedder.embed_texts(texts)
        await _replace_chunks(session, GDriveChunk, ds.id, user_id, texts, vectors)

    await session.flush()
    return user_id
