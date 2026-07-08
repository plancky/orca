import json
import uuid

import pytest

from backend.context.conversation import (
    _get_redis,
    append_turn_messages,
    get_conversation_context,
)
from backend.db.models import Conversation, Message, MessageRole, User
from backend.db.session import async_session_factory


@pytest.mark.asyncio
async def test_context_redis_rolling_push_and_read():
    import backend.context.conversation as conv_module

    conv_module._redis_client = None

    cid = uuid.uuid4()

    # Push 6 turns
    class DummyTaskResult:
        response = "hi"
        actions_taken = []
        pending_actions = []

    # Real DB Session instead of dummy to allow get_next_message_seq to work
    async with async_session_factory() as session:
        user = User(email=f"test_{uuid.uuid4().hex[:8]}@test.com", hashed_password="pw")
        session.add(user)
        await session.commit()
        uid = user.id

        conv = Conversation(id=cid, user_id=uid)
        session.add(conv)
        await session.commit()

        for i in range(6):
            await append_turn_messages(
                session=session,
                conversation_id=cid,
                user_id=uid,
                query=f"query {i}",
                task_result=DummyTaskResult(),
                task_id=None,
                intent={"intent": f"intent_{i}"},
                plan=None,
            )

    ctx = await get_conversation_context(str(uid), str(cid))
    assert len(ctx) == 5
    assert ctx[0]["query"] == "query 1"
    assert ctx[-1]["query"] == "query 5"
    assert ctx[-1]["intent"] == "intent_5"


@pytest.mark.asyncio
async def test_context_db_fallback():
    import backend.context.conversation as conv_module

    conv_module._redis_client = None

    cid = uuid.uuid4()

    async with async_session_factory() as session:
        user = User(email=f"test_{uuid.uuid4().hex[:8]}@test.com", hashed_password="pw")
        session.add(user)
        await session.commit()
        uid = user.id

        conv = Conversation(id=cid, user_id=uid)
        session.add(conv)
        await session.commit()

        for i in range(2):
            session.add(
                Message(
                    conversation_id=cid,
                    user_id=uid,
                    role=MessageRole.USER.value,
                    content=f"q{i}",
                    seq=i * 2,
                )
            )
            session.add(
                Message(
                    conversation_id=cid,
                    user_id=uid,
                    role=MessageRole.ASSISTANT.value,
                    content=f"r{i}",
                    seq=i * 2 + 1,
                    intent={"intent": f"int{i}"},
                )
            )
        await session.commit()

        # Flush redis so we are forced to fallback
        r = _get_redis()
        await r.delete(f"user:{uid}:conv:{cid}")

        ctx = await get_conversation_context(str(uid), str(cid), session=session)
        assert len(ctx) == 2
        assert ctx[0]["query"] == "q0"
        assert ctx[1]["query"] == "q1"
        assert ctx[1]["intent"] == "int1"


@pytest.mark.asyncio
async def test_context_injection_in_classifier(monkeypatch):
    import backend.context.conversation as conv_module

    conv_module._redis_client = None
    import backend.orchestration.stages.classifier as classifier

    classifier._redis_client = None

    import backend.orchestration.stages.classifier as classifier

    messages_captured = []

    async def mock_chat(self, messages, response_format, **kwargs):
        messages_captured.extend(messages)
        return json.dumps(
            {
                "services": ["gmail"],
                "intent": "read_email",
                "entities": {},
                "steps": [],
                "needs_clarification": False,
            }
        )

    monkeypatch.setattr("backend.llm.client.LLMClient.chat", mock_chat)

    uid = uuid.uuid4()
    cid = uuid.uuid4()

    r = _get_redis()
    key = f"user:{uid}:conv:{cid}"
    await r.rpush(
        key,
        json.dumps(
            {
                "query": "find the proposal",
                "intent": "search",
                "entities": {},
                "result_summary": "Here is the proposal email.",
            }
        ),
    )

    intent = await classifier.classify(
        query="that email about the proposal",
        user_id=str(uid),
        conversation_id=str(cid),
    )

    assert "Here is the proposal email." in messages_captured[0]["content"]
    assert intent.needs_clarification is False
