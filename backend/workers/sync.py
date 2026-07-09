"""Background worker logic for user corpus synchronization.

Wave D3 fills the per-user sync beat and its FastAPI routes.
"""

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select

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

    The mock corpus and the Google adapters both yield ``(upserts, removals,
    cursor)``, so the chunk/embed/write path below is identical for both. Each
    service is committed on its own and wrapped in a try/except: a failing
    service (dead token, quota, embed error) is rolled back and recorded as an
    error, without discarding the services that already succeeded in this pass.
    """
    uid = uuid.UUID(str(user_id))
    status_updates: dict = {}
    is_mock = settings.PROVIDER == "mock"

    async with async_session_factory() as session:
        now = datetime.now(timezone.utc)
        corpus = build_corpus(now) if is_mock else None

        for service, ds_model, chunk_model, key_field in _SERVICES:
            try:
                if corpus is not None:
                    upserts, removals, cursor = corpus.get(service, []), [], None
                else:
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

                await _write_status(session, uid, service, now, len(upserts), cursor)
                await session.commit()
                status_updates[service] = {
                    "last_synced_at": now.isoformat(),
                    "item_count": len(upserts),
                }
            except Exception as exc:
                await session.rollback()
                print(f"[sync] {service} sync failed for user {uid}: {exc}")
                status_updates[service] = {"error": str(exc)}

    return status_updates


@app.task(name="backend.workers.sync.sync_user")
def sync_user(user_id: str) -> dict:
    """On-demand sync of ONE user (POST /sync/trigger + the OAuth callback).

    Pulls from the live Google APIs when ``PROVIDER=google``, else the mock corpus.
    """
    return asyncio.run(sync_all_async(user_id))


@app.task(name="backend.workers.sync.sync_all_users")
def sync_all_users():
    """Beat task: sync active users (Google: only connected, non-invalid)."""

    async def _sync_active():
        async with async_session_factory() as session:
            stmt = select(User.id).where(User.is_active.is_(True))
            if settings.PROVIDER != "mock":
                stmt = stmt.where(
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
