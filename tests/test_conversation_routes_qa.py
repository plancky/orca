import uuid
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.core.security import create_access_token
from backend.db.models import Conversation, Message, User
from backend.db.session import async_session_factory
from backend.main import app


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(
        subject=str(user.id), expires_delta=timedelta(hours=1)
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def seeded():
    async with async_session_factory() as session:
        user = User(
            email=f"conv_qa_{uuid.uuid4().hex}@example.com", hashed_password="pw"
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conv = Conversation(user_id=user.id, title="Budget emails")
        session.add(conv)
        await session.commit()
        await session.refresh(conv)

        session.add(
            Message(
                conversation_id=conv.id,
                user_id=user.id,
                seq=0,
                role="user",
                content="Find emails from sarah about budget",
            )
        )
        session.add(
            Message(
                conversation_id=conv.id,
                user_id=user.id,
                seq=1,
                role="assistant",
                content="I found 2 emails from sarah about the budget.",
            )
        )
        await session.commit()
        return user, conv


@pytest.mark.asyncio
async def test_list_conversations(seeded):
    user, conv = seeded
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/conversations", headers=_auth(user))
        assert resp.status_code == 200
        data = resp.json()
        row = next(c for c in data if c["id"] == str(conv.id))
        assert row["title"] == "Budget emails"
        assert "updated_at" in row

    from backend.db.session import engine

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_conversation_detail_ordered(seeded):
    user, conv = seeded
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/v1/conversations/{conv.id}", headers=_auth(user)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(conv.id)
        seqs = [m["seq"] for m in data["messages"]]
        assert seqs == sorted(seqs)
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][1]["role"] == "assistant"

    from backend.db.session import engine

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_conversation_not_owned_404(seeded):
    _user, conv = seeded
    async with async_session_factory() as session:
        other = User(
            email=f"other_{uuid.uuid4().hex}@example.com", hashed_password="pw"
        )
        session.add(other)
        await session.commit()
        await session.refresh(other)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/api/v1/conversations/{conv.id}", headers=_auth(other)
        )
        assert resp.status_code == 404

    from backend.db.session import engine

    await engine.dispose()


@pytest.mark.asyncio
async def test_list_conversations_unauth_401():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/conversations")
        assert resp.status_code == 401

    from backend.db.session import engine

    await engine.dispose()


@pytest.mark.asyncio
async def test_delete_conversation_cascades_messages(seeded):
    user, conv = seeded
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete(
            f"/api/v1/conversations/{conv.id}", headers=_auth(user)
        )
        assert resp.status_code == 204

        resp = await client.get(
            f"/api/v1/conversations/{conv.id}", headers=_auth(user)
        )
        assert resp.status_code == 404

    async with async_session_factory() as session:
        remaining = (
            (
                await session.execute(
                    select(Message).where(Message.conversation_id == conv.id)
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []

    from backend.db.session import engine

    await engine.dispose()


@pytest.mark.asyncio
async def test_delete_conversation_not_owned_404(seeded):
    _user, conv = seeded
    async with async_session_factory() as session:
        other = User(
            email=f"other_{uuid.uuid4().hex}@example.com", hashed_password="pw"
        )
        session.add(other)
        await session.commit()
        await session.refresh(other)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete(
            f"/api/v1/conversations/{conv.id}", headers=_auth(other)
        )
        assert resp.status_code == 404

    async with async_session_factory() as session:
        still_there = await session.get(Conversation, conv.id)
        assert still_there is not None

    from backend.db.session import engine

    await engine.dispose()


@pytest.mark.asyncio
async def test_delete_conversation_missing_404(seeded):
    user, _conv = seeded
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete(
            f"/api/v1/conversations/{uuid.uuid4()}", headers=_auth(user)
        )
        assert resp.status_code == 404

    from backend.db.session import engine

    await engine.dispose()


@pytest.mark.asyncio
async def test_delete_conversation_unauth_401(seeded):
    _user, conv = seeded
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete(f"/api/v1/conversations/{conv.id}")
        assert resp.status_code == 401

    from backend.db.session import engine

    await engine.dispose()
