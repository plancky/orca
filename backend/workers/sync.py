"""Background worker logic for user corpus synchronization.

Wave D3 fills the per-user sync beat and its FastAPI routes.
"""

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from backend.config import settings
from backend.db.models import (
    GCalChunk,
    GCalDatasource,
    GDriveChunk,
    GDriveDatasource,
    GmailChunk,
    GmailDatasource,
    SyncStatus,
    User,
)
from backend.db.session import async_session_factory
from backend.embeddings.chunkers import chunk_gcal, chunk_gdrive, chunk_gmail
from backend.embeddings.embedder import embedder
from backend.providers.mock._corpus_data import build_corpus
from backend.providers.mock.seed_corpus import (
    _chunk_or_fallback,
    _replace_chunks,
    _upsert_datasource,
)
from backend.workers.celery_app import app


async def sync_all_async(user_id: str) -> dict:
    """Sync per-user: fetch -> upsert datasource -> chunk+embed -> write."""
    uid = uuid.UUID(str(user_id))
    status_updates = {}

    async with async_session_factory() as session:
        # fetch items (mock: refresh seed-corpus deltas)
        if settings.PROVIDER == "mock":
            corpus = build_corpus(datetime.now(timezone.utc))
        else:
            corpus = {"gmail": [], "gcal": [], "gdrive": []}

        now = datetime.now(timezone.utc)

        # Gmail
        for item in corpus.get("gmail", []):
            ds = await _upsert_datasource(
                session, GmailDatasource, "email_id", uid, item
            )
            texts = _chunk_or_fallback(
                chunk_gmail,
                (item.get("subject") or "", item["content"]),
                f"{item.get('subject') or ''}\n{item['content']}",
            )
            vectors = await embedder.embed_texts(texts)
            await _replace_chunks(
                session,
                GmailChunk,
                ds.id,
                uid,
                texts,
                vectors,
                thread_id=item.get("thread_id"),
            )

        # GCal
        for item in corpus.get("gcal", []):
            ds = await _upsert_datasource(
                session, GCalDatasource, "event_id", uid, item
            )
            title = item.get("title") or ""
            desc = item.get("description") or ""
            loc = item.get("location") or ""
            texts = _chunk_or_fallback(
                chunk_gcal,
                (title, desc, loc),
                f"{title}\n{desc}\n{loc}",
            )
            vectors = await embedder.embed_texts(texts)
            await _replace_chunks(session, GCalChunk, ds.id, uid, texts, vectors)

        # GDrive
        for item in corpus.get("gdrive", []):
            ds = await _upsert_datasource(
                session, GDriveDatasource, "file_id", uid, item
            )
            texts = _chunk_or_fallback(
                chunk_gdrive, (item["content"],), item["content"]
            )
            vectors = await embedder.embed_texts(texts)
            await _replace_chunks(session, GDriveChunk, ds.id, uid, texts, vectors)

        # Update sync status
        for service in ["gmail", "gcal", "gdrive"]:
            items = corpus.get(service, [])
            existing = (
                await session.execute(
                    select(SyncStatus).where(
                        SyncStatus.user_id == uid, SyncStatus.service == service
                    )
                )
            ).scalars().first()
            if existing is None:
                existing = SyncStatus(user_id=uid, service=service)
                session.add(existing)

            existing.last_synced_at = now
            existing.item_count = len(items)
            status_updates[service] = {
                "last_synced_at": now.isoformat(),
                "item_count": len(items),
            }

        await session.commit()

    return status_updates


@app.task(name="backend.workers.sync.sync_all_users")
def sync_all_users():
    """Beat task: sync all active users."""

    async def _sync_active():
        async with async_session_factory() as session:
            users = (
                await session.execute(select(User).where(User.is_active.is_(True)))
            ).scalars().all()
            for u in users:
                await sync_all_async(str(u.id))

    asyncio.run(_sync_active())
