import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.core.security import create_access_token
from backend.db.models import GmailChunk, GmailDatasource, User
from backend.db.session import async_session_factory
from backend.embeddings.search import hybrid_search
from backend.main import app
from backend.workers.sync import sync_all_async

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def user_fixture():
    uid = uuid.uuid4()
    async with async_session_factory() as session:
        user = User(
            id=uid,
            email=f"test_sync_{uid}@example.com",
            hashed_password="fake"
        )
        session.add(user)
        await session.commit()
        return user


async def test_sync_happy_path(user_fixture):
    uid = user_fixture.id

    # Run sync
    _ = await sync_all_async(str(uid))

    async with async_session_factory() as session:
        gmail_ds = (
            await session.execute(
                select(GmailDatasource).where(GmailDatasource.user_id == uid)
            )
        ).scalars().all()
        assert len(gmail_ds) == 8

        gmail_chunks = (
            await session.execute(
                select(GmailChunk).where(GmailChunk.user_id == uid)
            )
        ).scalars().all()
        assert len(gmail_chunks) >= 8
        assert all(c.embedding is not None for c in gmail_chunks)

        from backend.testing.fakes import FakeEmbedder

        embedder = FakeEmbedder()
        q_vec = await embedder.embed_query("turkish airlines")
        search_res = await hybrid_search(session, q_vec, "gmail", str(uid))
        assert len(search_res) > 0

    from datetime import timedelta
    token = create_access_token(uid, expires_delta=timedelta(minutes=15))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/sync/status", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        statuses = resp.json()
        assert len(statuses) == 3
        gmail_status = next(s for s in statuses if s["service"] == "gmail")
        assert gmail_status["item_count"] == 8
        assert gmail_status["last_synced_at"] is not None

        # Idempotency
        _ = await sync_all_async(str(uid))

        async with async_session_factory() as session:
            gmail_ds2 = (
                await session.execute(
                    select(GmailDatasource).where(GmailDatasource.user_id == uid)
                )
            ).scalars().all()
            assert len(gmail_ds2) == 8

        resp2 = await client.post(
            "/api/v1/sync/trigger", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "enqueued"
