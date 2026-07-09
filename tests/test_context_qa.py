import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.context.conversation import (
    _get_redis,
    append_turn_messages,
    get_conversation_context,
)
from backend.db.models import Conversation, Message, MessageRole, Task, User
from backend.db.session import async_session_factory
from backend.llm.client import llm_client
from backend.orchestration.models.intent import Intent
from backend.synth.synthesizer import synthesize


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


@pytest.mark.asyncio
async def test_query_turn_persists_messages_with_uuid_node_outputs(stub_llm_factory):
    import backend.context.conversation as conv_module

    conv_module._redis_client = None
    stub_llm_factory(json.dumps({"response": "ok", "actions_taken": []}))

    async with async_session_factory() as session:
        user = User(email=f"test_{uuid.uuid4().hex[:8]}@test.com", hashed_password="pw")
        session.add(user)
        await session.commit()
        uid = user.id

        cid = uuid.uuid4()
        conv = Conversation(id=cid, user_id=uid)
        session.add(conv)
        await session.commit()

        task_id = uuid.uuid4()
        task = Task(id=task_id, user_id=uid, conversation_id=cid)
        session.add(task)
        await session.commit()

        node_outputs = {
            "n1": {
                "id": uuid.uuid4(),
                "received_at": datetime.now(timezone.utc),
            }
        }
        intent = Intent(services=["gmail"], intent="find")

        result = await synthesize(intent, node_outputs, None, llm_client=llm_client)

        await append_turn_messages(
            session,
            cid,
            uid,
            "q",
            result,
            task_id,
            intent=intent.model_dump(mode="json"),
        )

        rows = (
            await session.execute(select(Message).where(Message.conversation_id == cid))
        ).scalars().all()
        assert {m.role for m in rows} == {"user", "assistant"}
        assert len(rows) == 2
