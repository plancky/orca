import uuid
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.config import settings
from backend.core.security import create_access_token
from backend.db.models import GmailChunk, GmailDatasource, SyncStatus, User
from backend.db.session import async_session_factory
from backend.embeddings.search import hybrid_search
from backend.main import app
from backend.providers.mock.seed_corpus import seed_corpus
from backend.workers.sync import sync_all_async

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def user_fixture():
    uid = uuid.uuid4()
    async with async_session_factory() as session:
        user = User(
            id=uid,
            email=f"test_sync_{uid}@example.com",
            hashed_password="fake",
        )
        session.add(user)
        await session.commit()
        return user


async def test_seed_corpus_demo_path():
    """The one-time demo seed (scripts/seed.py) still populates + embeds the
    mock corpus for the resolved demo/seed user, independent of the sync
    worker below."""
    async with async_session_factory() as session:
        seed_uid = await seed_corpus(session)
        await session.commit()

        gmail_ds = (
            await session.execute(
                select(GmailDatasource).where(GmailDatasource.user_id == seed_uid)
            )
        ).scalars().all()
        assert len(gmail_ds) == 8

        gmail_chunks = (
            await session.execute(
                select(GmailChunk).where(GmailChunk.user_id == seed_uid)
            )
        ).scalars().all()
        assert len(gmail_chunks) >= 8
        assert all(c.embedding is not None for c in gmail_chunks)

        from backend.testing.fakes import FakeEmbedder

        embedder = FakeEmbedder()
        q_vec = await embedder.embed_query("turkish airlines")
        search_res = await hybrid_search(session, q_vec, "gmail", str(seed_uid))
        assert len(search_res) > 0


async def test_sync_worker_is_noop_for_non_google_provider(user_fixture, monkeypatch):
    """sync_all_async must never stamp the mock corpus onto an arbitrary real
    user: every active user used to get the same demo data on every beat pass
    and every /sync/trigger call, regardless of whether they seeded anything.
    """
    monkeypatch.setattr(settings, "PROVIDER", "mock")
    uid = user_fixture.id

    result = await sync_all_async(str(uid))
    assert result == {}

    async with async_session_factory() as session:
        gmail_ds = (
            await session.execute(
                select(GmailDatasource).where(GmailDatasource.user_id == uid)
            )
        ).scalars().all()
        assert gmail_ds == []

        statuses = (
            await session.execute(
                select(SyncStatus).where(SyncStatus.user_id == uid)
            )
        ).scalars().all()
        assert statuses == []

    token = create_access_token(uid, expires_delta=timedelta(minutes=15))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/sync/status", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json() == []

        # Trigger endpoint enqueues sync_user for the caller; celery eager mode
        # would run it via asyncio.run inside the running loop and fail, so we
        # patch the task's delay to a no-op.
        from unittest.mock import patch
        with patch("backend.workers.sync.sync_user.delay"):
            resp2 = await client.post(
                "/api/v1/sync/trigger", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "enqueued"
