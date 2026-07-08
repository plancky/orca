import json
import uuid

import pytest
from sqlalchemy import select

from backend.db.models import ActionsLog, Conversation, Message, Task, User
from backend.db.session import async_session_factory
from backend.workers.confirm import resume
from backend.workers.orchestrate import pipeline


class FakeLLMClient:
    def __init__(self):
        self.responses = []
        self.call_count = 0

    async def chat(self, messages, response_format=None, **kwargs):
        if self.call_count >= len(self.responses):
            return self.responses[-1]
        resp = self.responses[self.call_count]
        self.call_count += 1
        return resp

# Shared global mock
global_fake = FakeLLMClient()

@pytest.fixture(autouse=True)
def mock_all_llm(monkeypatch):
    import backend.llm.client
    monkeypatch.setattr(backend.llm.client.LLMClient, "chat", global_fake.chat)

@pytest.mark.asyncio
async def test_pipeline_happy():
    global_fake.call_count = 0
    global_fake.responses = [
        json.dumps({
            "services": ["gmail"],
            "intent": "search_emails",
            "entities": {},
            "steps": ["search"],
            "needs_clarification": False
        }),
        json.dumps({
            "nodes": [
                {
                    "id": "n1",
                    "tool": "gmail.search_emails",
                    "args": {"query": "budget"},
                    "optional": False,
                    "depends_on": [],
                }
            ]
        }),
        json.dumps({"response": "Here are your emails.", "actions_taken": []})
    ]

    async with async_session_factory() as session:
        user = User(email=f"test_{uuid.uuid4().hex[:8]}@test.com", hashed_password="pw")
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conv = Conversation(user_id=user.id)
        session.add(conv)
        await session.commit()
        await session.refresh(conv)

        task = Task(user_id=user.id, conversation_id=conv.id)
        session.add(task)
        await session.commit()
        await session.refresh(task)

    res = await pipeline(
        str(task.id),
        str(user.id),
        "emails from sarah about budget",
        str(conv.id),
    )
    assert res["status"] == "success", res.get("error")

    async with async_session_factory() as session:
        task_row = await session.get(Task, task.id)
        assert task_row.status == "success"
        assert task_row.result is not None
        assert "response" in task_row.result

        stmt = (
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.seq)
        )
        messages = (await session.execute(stmt)).scalars().all()
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].seq == 0
        assert messages[1].role == "assistant"
        assert messages[1].seq == 1
        assert messages[1].task_id == task.id

@pytest.mark.asyncio
async def test_pipeline_write_resume():
    global_fake.call_count = 0
    global_fake.responses = [
        json.dumps({
            "services": ["gmail"],
            "intent": "send_email",
            "entities": {},
            "steps": ["send"],
            "needs_clarification": False
        }),
        json.dumps({
            "nodes": [
                {
                    "id": "n1",
                    "tool": "gmail.send_email",
                    "args": {
                        "to": "test@test.com",
                        "subject": "hi",
                        "body": "hi",
                    },
                    "optional": False,
                    "depends_on": [],
                }
            ]
        }),
        json.dumps(
            {"response": "Email sent (not really, denied).", "actions_taken": []}
        )
    ]

    async with async_session_factory() as session:
        user = User(
            email=f"write_{uuid.uuid4().hex[:8]}@test.com",
            hashed_password="pw",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        conv = Conversation(user_id=user.id)
        session.add(conv)
        await session.commit()
        await session.refresh(conv)

        task = Task(user_id=user.id, conversation_id=conv.id)
        session.add(task)
        await session.commit()
        await session.refresh(task)

    res = await pipeline(
        str(task.id),
        str(user.id),
        "send an email",
        str(conv.id),
    )
    assert res["status"] == "awaiting_confirmation", res.get("error")

    async with async_session_factory() as session:
        task_row = await session.get(Task, task.id)
        assert task_row.status == "awaiting_confirmation"
        assert task_row.checkpoint is not None

        stmt = select(ActionsLog).where(ActionsLog.task_id == task.id)
        action_log = (await session.execute(stmt)).scalars().first()
        assert action_log is not None
        assert action_log.status == "pending"

        new_task = Task(
            user_id=user.id,
            conversation_id=conv.id,
            parent_task_id=task.id,
        )
        session.add(new_task)
        await session.commit()
        await session.refresh(new_task)

        checkpoint_json = json.dumps(task_row.checkpoint)

    # Resume with "deny"
    res2 = await resume(checkpoint_json, "deny", str(new_task.id), str(user.id))
    assert res2["status"] == "success", res2.get("error")

    async with async_session_factory() as session:
        new_task_row = await session.get(Task, new_task.id)
        assert new_task_row.status == "success"

        # Check action log is denied
        stmt = select(ActionsLog).where(ActionsLog.task_id == task.id)
        action_log = (await session.execute(stmt)).scalars().first()
        assert action_log.status == "denied"
