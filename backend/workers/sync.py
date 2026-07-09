"""Background worker logic for user corpus synchronization.

Wave D3 fills the per-user sync beat and its FastAPI routes.
"""

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

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
from backend.providers.mock.seed_corpus import (
    _chunk_or_fallback,
    _replace_chunks,
    _upsert_datasource,
)
from backend.workers.celery_app import app

_SERVICES = (
    ("gmail", GmailDatasource, GmailChunk, "email_id"),
    ("gcal", GCalDatasource, GCalChunk, "event_id"),
    ("gdrive", GDriveDatasource, GDriveChunk, "file_id"),
)


def _chunk_texts(service: str, item: dict) -> list[str]:
    if service == "gmail":
        return _chunk_or_fallback(
            chunk_gmail,
            (item.get("subject") or "", item.get("content") or ""),
            f"{item.get('subject') or ''}\n{item.get('content') or ''}",
        )
    if service == "gcal":
        title = item.get("title") or ""
        desc = item.get("description") or ""
        loc = item.get("location") or ""
        return _chunk_or_fallback(
            chunk_gcal, (title, desc, loc), f"{title}\n{desc}\n{loc}"
        )
    return _chunk_or_fallback(
        chunk_gdrive, (item.get("content") or "",), item.get("content") or ""
    )


async def _load_cursor(session, uid, service: str) -> str | None:
    row = (
        await session.execute(
            select(SyncStatus).where(
                SyncStatus.user_id == uid, SyncStatus.service == service
            )
        )
    ).scalars().first()
    return row.cursor if row else None


async def _fetch_google(session, uid, service: str, cursor: str | None):
    """Fetch one service's delta from the live Google API.

    The blocking ``googleapiclient`` pagination is pushed to a worker thread so
    it never stalls the event loop (matching ``GoogleProvider.get``). Failures
    propagate to the per-service handler in ``sync_all_async`` — which rolls the
    service back and records the error — instead of being swallowed here. A
    swallowed fetch used to bump ``last_synced_at`` with 0 items, masking a dead
    token as a healthy empty sync.
    """
    from backend.providers.google.provider import adapter_for, build_service

    adapter = adapter_for(service)
    client = await build_service(
        session, uid, adapter.SERVICE_NAME, adapter.SERVICE_VERSION
    )
    return await asyncio.to_thread(adapter.sync, client, cursor)


async def _delete_removed(session, ds_model, key_field, uid, removals) -> None:
    await session.execute(
        delete(ds_model).where(
            ds_model.user_id == uid,
            getattr(ds_model, key_field).in_(removals),
        )
    )


async def _count_datasource(session, ds_model, uid) -> int:
    """Total rows the user currently has, not this run's delta upserts."""
    return (
        await session.execute(
            select(func.count()).select_from(ds_model).where(ds_model.user_id == uid)
        )
    ).scalar_one()


async def _write_status(session, uid, service, now, count, cursor) -> None:
    row = (
        await session.execute(
            select(SyncStatus).where(
                SyncStatus.user_id == uid, SyncStatus.service == service
            )
        )
    ).scalars().first()
    if row is None:
        row = SyncStatus(user_id=uid, service=service)
        session.add(row)
    row.last_synced_at = now
    row.item_count = count
    if cursor is not None:
        row.cursor = cursor


async def sync_all_async(user_id: str) -> dict:
    """Per-user sync: fetch -> upsert datasource -> chunk+embed -> replace chunks.

    Live-source only: a no-op unless ``PROVIDER=="google"``. The mock corpus is
    seeded once via ``scripts/seed.py`` (``providers.mock.seed_corpus``) for the
    demo/dev account, not repeatedly written into every active user's tables by
    this worker. Each service commits on its own inside its own try/except: a
    failing service (dead token, quota, embed error) rolls back without
    discarding services that already succeeded in this pass.
    """
    uid = uuid.UUID(str(user_id))
    if settings.PROVIDER != "google":
        return {}
    status_updates: dict = {}

    async with async_session_factory() as session:
        now = datetime.now(timezone.utc)

        for service, ds_model, chunk_model, key_field in _SERVICES:
            try:
                stored = await _load_cursor(session, uid, service)
                upserts, removals, cursor = await _fetch_google(
                    session, uid, service, stored
                )

                for item in upserts:
                    ds = await _upsert_datasource(
                        session, ds_model, key_field, uid, item
                    )
                    texts = _chunk_texts(service, item)
                    vectors = await embedder.embed_texts(texts)
                    await _replace_chunks(
                        session,
                        chunk_model,
                        ds.id,
                        uid,
                        texts,
                        vectors,
                        thread_id=item.get("thread_id") if service == "gmail" else None,
                    )

                if removals:
                    await _delete_removed(session, ds_model, key_field, uid, removals)

                total = await _count_datasource(session, ds_model, uid)
                await _write_status(session, uid, service, now, total, cursor)
                await session.commit()
                status_updates[service] = {
                    "last_synced_at": now.isoformat(),
                    "item_count": total,
                }
            except Exception as exc:
                await session.rollback()
                print(f"[sync] {service} sync failed for user {uid}: {exc}")
                status_updates[service] = {"error": str(exc)}

    return status_updates


@app.task(name="backend.workers.sync.sync_user")
def sync_user(user_id: str) -> dict:
    """On-demand sync of ONE user (POST /sync/trigger + the OAuth callback).

    A no-op unless ``PROVIDER=="google"`` — see ``sync_all_async``.
    """
    return asyncio.run(sync_all_async(user_id))


@app.task(name="backend.workers.sync.sync_all_users")
def sync_all_users():
    """Beat task: sync active, Google-connected users. No-op for other providers."""

    async def _sync_active():
        if settings.PROVIDER != "google":
            return
        async with async_session_factory() as session:
            stmt = select(User.id).where(
                User.is_active.is_(True),
                User.google_refresh_token.is_not(None),
                User.auth_status.is_distinct_from("invalid"),
            )
            user_ids = (await session.execute(stmt)).scalars().all()
        # Per-user isolation: one dead token must not abort the rest of the pass.
        for uid in user_ids:
            try:
                await sync_all_async(str(uid))
            except Exception as exc:
                print(f"[sync] user {uid} sync failed: {exc}")

    asyncio.run(_sync_active())
