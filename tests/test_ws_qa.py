import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.api.deps import get_redis
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
async def test_ws_qa_invalid_token():
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/query?token=bad") as ws:
            ws.receive_json()
    assert exc_info.value.code == 1008
