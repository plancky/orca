import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from backend.db.models import Conversation, User
from backend.db.session import async_session_factory
from backend.main import app
from backend.workers.celery_app import app as capp

# Apply celery eager mode
capp.conf.task_always_eager = True
capp.conf.task_eager_propagates = True


@pytest.fixture
async def user_fixture():
    async with async_session_factory() as session:
        user = User(
            email=f"test_qa_{uuid.uuid4().hex}@example.com", hashed_password="pw"
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.mark.asyncio
async def test_query_route_happy(user_fixture, monkeypatch):
    from datetime import timedelta

    from backend.core.security import create_access_token

    auth_token = create_access_token(
        subject=str(user_fixture.id), expires_delta=timedelta(hours=1)
    )
    from backend.workers.orchestrate import run_pipeline

    monkeypatch.setattr(run_pipeline, "delay", lambda *args, **kwargs: None)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. POST /api/v1/query (NO conv_id)
        resp = await client.post(
            "/api/v1/query",
            json={"query": "Find emails"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "task_id" in data
        assert "conversation_id" in data
        assert data["status"] == "queued"

        task_id = data["task_id"]
        conv_id = data["conversation_id"]

        # 2. GET /api/v1/tasks/{task_id}
        resp = await client.get(
            f"/api/v1/tasks/{task_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert resp.status_code == 200
        task_data = resp.json()
        assert task_data["status"] == "queued"

        # 3. Assert Conversation exists
        async with async_session_factory() as session:
            conv = await session.get(Conversation, uuid.UUID(conv_id))
            assert conv is not None
            assert conv.title == "Find emails"

    from backend.db.session import engine

    await engine.dispose()


@pytest.mark.asyncio
async def test_query_route_failure(user_fixture, monkeypatch):
    from datetime import timedelta

    from backend.core.security import create_access_token

    auth_token = create_access_token(
        subject=str(user_fixture.id), expires_delta=timedelta(hours=1)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # GET a different user's task_id -> 404
        # We just use a random uuid, since it won't exist for the user
        random_task_id = str(uuid.uuid4())
        resp = await client.get(
            f"/api/v1/tasks/{random_task_id}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert resp.status_code == 404

        # POST /query with confirm action_id that doesn't exist -> 404
        random_action_id = str(uuid.uuid4())
        resp = await client.post(
            "/api/v1/query",
            json={
                "query": "Yes",
                "conversation_id": str(uuid.uuid4()),
                "confirm": {"action_id": random_action_id, "decision": "approved"},
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert resp.status_code == 404

    from backend.db.session import engine

    await engine.dispose()
