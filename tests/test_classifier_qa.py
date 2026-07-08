from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.orchestration.stages.classifier import classify

FIXTURE_DIR = Path("backend/orchestration/stages/tests/fixtures/llm/classifier")


def load_fixture(name: str) -> str:
    return (FIXTURE_DIR / f"{name}.json").read_text()


@pytest.mark.asyncio
async def test_single_gcal(stub_llm_factory):
    stub_llm_factory(load_fixture("single_gcal"))
    intent = await classify("What's on my calendar next week?")
    assert set(intent.services) == {"gcal"}
    assert intent.needs_clarification is False
    assert "timeframe" in intent.entities


@pytest.mark.asyncio
async def test_single_gmail(stub_llm_factory):
    stub_llm_factory(load_fixture("single_gmail"))
    intent = await classify("Find emails from sarah@company.com about the budget")
    assert set(intent.services) == {"gmail"}
    assert intent.entities.get("sender") == "sarah@company.com"


@pytest.mark.asyncio
async def test_single_drive(stub_llm_factory):
    stub_llm_factory(load_fixture("single_drive"))
    intent = await classify("Show me PDFs in Drive from last month")
    assert set(intent.services) == {"drive"}
    assert "timeframe" in intent.entities


@pytest.mark.asyncio
async def test_multi_cancel(stub_llm_factory):
    stub_llm_factory(load_fixture("multi_cancel"))
    intent = await classify("Cancel my Turkish Airlines flight")
    assert set(intent.services) == {"gmail", "gcal"}
    assert intent.entities.get("airline") == "Turkish Airlines"


@pytest.mark.asyncio
async def test_multi_prepare(stub_llm_factory):
    stub_llm_factory(load_fixture("multi_prepare"))
    intent = await classify("Prepare for tomorrow's meeting with Acme Corp")
    assert set(intent.services) == {"gcal", "gmail", "drive"}
    assert "timeframe" in intent.entities


@pytest.mark.asyncio
async def test_multi_conflict(stub_llm_factory):
    stub_llm_factory(load_fixture("multi_conflict"))
    intent = await classify(
        "Find events next week that conflict with my out-of-office doc"
    )
    assert set(intent.services) == {"gcal", "drive"}
    assert "timeframe" in intent.entities


@pytest.mark.asyncio
async def test_hard_ambiguous(stub_llm_factory):
    stub_llm_factory(load_fixture("hard_ambiguous"))
    intent = await classify("Move the meeting with John")
    assert intent.needs_clarification is True
    assert intent.clarification is not None


@pytest.mark.asyncio
async def test_hard_proposal_empty(stub_llm_factory):
    stub_llm_factory(load_fixture("hard_proposal_empty"))
    intent = await classify("That email about the proposal", context=[])
    assert intent.needs_clarification is True


@pytest.mark.asyncio
async def test_hard_proposal_context(stub_llm_factory):
    stub_llm_factory(load_fixture("hard_proposal_context"))
    intent = await classify(
        "That email about the proposal",
        context=[{"role": "user", "content": "Let's review the Acme proposal"}],
    )
    assert set(intent.services) == {"gmail"}
    assert intent.needs_clarification is False


@pytest.mark.asyncio
async def test_hard_next_tuesday(stub_llm_factory, frozen_clock, fixed_tz):
    stub_llm_factory(load_fixture("hard_next_tuesday"))
    tz = ZoneInfo(fixed_tz)
    intent = await classify("Next Tuesday", now=frozen_clock, tz=tz)
    assert set(intent.services) == {"gcal"}

    tf = intent.entities.get("timeframe")
    assert tf is not None
    # frozen_clock is 2024-03-10 12:00:00 (Sunday)
    # Next Tuesday is 2024-03-12
    # start: 2024-03-12T00:00:00-04:00 (EDT starts Mar 10 at 2am)
    # end datetime.max limit checked below
    assert tf["start"] == "2024-03-12T00:00:00-04:00"
    assert tf["end"] == "2024-03-12T23:59:59.999999-04:00"


@pytest.mark.asyncio
async def test_malformed_repair(stub_llm_factory):
    malformed = load_fixture("malformed")
    repaired = load_fixture("malformed_repair")

    call_count = 0

    def fake_response(messages):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return malformed
        return repaired

    stub_llm_factory(fake_response)

    intent = await classify("What's on my calendar next week?")
    assert call_count == 2
    assert set(intent.services) == {"gcal"}
    assert intent.needs_clarification is False
