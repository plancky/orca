import os

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ["EMBED_MODE"] = "fake"

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

FIXED_TZ = "America/New_York"
# 2024-03-10 is the US spring-forward DST boundary — catches off-by-one-hour tz bugs.
FROZEN_NOW = dt.datetime(2024, 3, 10, 12, 0, 0, tzinfo=ZoneInfo(FIXED_TZ))


@pytest.fixture
def fixed_tz() -> str:
    return FIXED_TZ


@pytest.fixture
def frozen_clock() -> dt.datetime:
    return FROZEN_NOW


@pytest.fixture
def stub_llm_factory(monkeypatch):
    def _install(response):
        async def _fake_chat(self, messages, response_format=None, temperature=0):
            return response(messages) if callable(response) else response

        monkeypatch.setattr("backend.llm.client.LLMClient.chat", _fake_chat)
        return _fake_chat

    return _install
