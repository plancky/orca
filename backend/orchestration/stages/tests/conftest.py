"""Co-located JSON-contract test harness for the classifier & planner stages.

Exposes a ``stub_llm`` fixture that replays the recorded completions under
``fixtures/llm/{classifier,planner}/`` by patching ``LLMClient.chat`` at the
**class level** (``backend.llm.client.LLMClient.chat``) — NEVER the
``llm_client`` singleton instance, which would leak the patch across tests (the
barrier-E isolation lesson). ``monkeypatch`` restores the class attribute after
every test, so each case starts from the real method.

The root ``tests/conftest.py`` lives in a sibling subtree (``tests/``) that does
NOT cover this package, so the frozen clock + fixed tz it exposes are mirrored
here for the temporal cases.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

FIXED_TZ = "America/New_York"
# 2024-03-10 is the US spring-forward DST boundary — mirrors tests/conftest.py so
# temporal cases resolve to EDT (-04:00) and catch off-by-one-hour tz bugs.
FROZEN_NOW = dt.datetime(2024, 3, 10, 12, 0, 0, tzinfo=ZoneInfo(FIXED_TZ))

_FIXTURES = Path(__file__).parent / "fixtures" / "llm"


def load_fixture(category: str, name: str) -> str:
    """Raw recorded completion for ``fixtures/llm/<category>/<name>.json``."""
    return (_FIXTURES / category / f"{name}.json").read_text()


@pytest.fixture
def fixed_tz() -> str:
    return FIXED_TZ


@pytest.fixture
def frozen_clock() -> dt.datetime:
    return FROZEN_NOW


class StubLLM:
    """Replays recorded fixtures through a class-level ``LLMClient.chat`` patch."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        self.calls: list[list[dict]] = []

    def install(self, responder: str | Callable[[list[dict]], str]) -> StubLLM:
        calls = self.calls

        async def _chat(_self, messages, response_format=None, temperature=0):
            calls.append(messages)
            return responder(messages) if callable(responder) else responder

        # CLASS-level patch — auto-reverted by monkeypatch, never the singleton.
        self._monkeypatch.setattr("backend.llm.client.LLMClient.chat", _chat)
        return self

    def replay(self, category: str, name: str) -> StubLLM:
        return self.install(load_fixture(category, name))

    def sequence(self, category: str, names: list[str]) -> StubLLM:
        payloads = [load_fixture(category, n) for n in names]
        calls = self.calls

        def _responder(_messages: list[dict]) -> str:
            return payloads[min(len(calls) - 1, len(payloads) - 1)]

        return self.install(_responder)

    def classifier(self, name: str) -> StubLLM:
        return self.replay("classifier", name)

    def planner(self, name: str) -> StubLLM:
        return self.replay("planner", name)


@pytest.fixture
def stub_llm(monkeypatch: pytest.MonkeyPatch) -> StubLLM:
    return StubLLM(monkeypatch)
