import json
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.api.deps import get_redis
from backend.core.security import create_access_token
from backend.db.models import Conversation, Task, TaskStatus, User
from backend.db.session import async_session_factory
from backend.main import app


class FakeRedis:
    def __init__(self):
        self.streams = {}

    async def incr(self, key):
        return 1
    
    async def expire(self, key, time):
        pass

    async def xadd(self, key, fields):
        if key not in self.streams:
            self.streams[key] = []
        msg_id = f"1000-{len(self.streams[key])}"
        self.streams[key].append((msg_id, fields))
        return msg_id

    async def xread(self, streams, count=None, block=None):
        res = []
        for key, last_id in streams.items():
            if key in self.streams:
                msgs = self.streams[key]
                idx = 0
                if last_id != "0-0" and last_id != "$":
                    for i, (mid, _) in enumerate(msgs):
                        if mid == last_id:
                            idx = i + 1
                            break
                if idx < len(msgs):
                    res.append((key, msgs[idx : idx + count] if count else msgs[idx:]))
        return res


fake_redis = FakeRedis()


async def override_get_redis():
    return fake_redis


app.dependency_overrides[get_redis] = override_get_redis


@pytest.mark.asyncio
async def test_ws_qa_happy():
    user = User(
        id=uuid.uuid4(), email=f"ws{uuid.uuid4()}@example.com", hashed_password="dummy"
    )
    conv = Conversation(id=uuid.uuid4(), user_id=user.id, title="WS Test")
    task = Task(
        id=uuid.uuid4(),
        user_id=user.id,
        conversation_id=conv.id,
        status=TaskStatus.SUCCESS.value,
        result={"answer": "hello"},
    )

    async with async_session_factory() as session:
        session.add(user)
        await session.flush()
        session.add(conv)
        await session.flush()
        session.add(task)
        await session.commit()

    stream_key = f"stream:tasks:{task.id}"
    await fake_redis.xadd(
        stream_key,
        {
            "data": json.dumps(
                {
                    "type": "node_started",
                    "task_id": str(task.id),
                    "node_id": "test",
                    "timestamp": "2024-01-01T",
                    "payload": {},
                }
            )
        },
    )

    token = create_access_token(user.id, expires_delta=timedelta(minutes=15))

    client = TestClient(app)
    with client.websocket_connect(f"/ws/query?token={token}") as ws:
        ws.send_json({"task_id": str(task.id)})

        msg1 = ws.receive_json()
        assert msg1["type"] == "node_started"

        msg2 = ws.receive_json()
        assert msg2["type"] == "done"
        assert msg2["payload"] == {"answer": "hello"}


@pytest.mark.asyncio
async def test_ws_qa_invalid_token():
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/query?token=bad") as ws:
            ws.receive_json()
    assert exc_info.value.code == 1008
