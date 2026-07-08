import json
import uuid

import pytest

from backend.llm.client import llm_client
from backend.orchestration.models.intent import Intent
from backend.orchestration.models.results import PendingAction, TaskResult
from backend.synth.synthesizer import synthesize

pytestmark = pytest.mark.anyio


async def test_synth_happy_path(stub_llm_factory):
    canned_resp = {
        "response": "Found it.\n✓ Searched emails.",
        "actions_taken": [
            {
                "tool": "gmail.search_emails",
                "args": {"query": "flight"},
                "result": "Found 1 flight",
                "status": "executed",
            }
        ],
    }
    stub_llm_factory(json.dumps(canned_resp))

    intent = Intent(services=["gmail"], intent="Find my flight")
    node_outputs = {"$n1": {"flight": "Turkish Airlines ABC123XYZ"}}
    pending = [PendingAction(action_id=uuid.uuid4(), tool="dummy")]

    result = await synthesize(
        intent, node_outputs, pending_actions=pending, llm_client=llm_client
    )

    assert isinstance(result, TaskResult)
    assert result.response == "Found it.\n✓ Searched emails."
    assert len(result.actions_taken) == 1
    assert result.actions_taken[0].tool == "gmail.search_emails"
    assert result.pending_actions == pending


async def test_synth_clarify_path():
    intent = Intent(
        services=[],
        intent="Move meeting",
        needs_clarification=True,
        clarification="Which meeting?",
    )

    result = await synthesize(intent, {}, pending_actions=[], llm_client=None)

    assert isinstance(result, TaskResult)
    assert result.response == "Which meeting?"
    assert result.actions_taken == []


async def test_synth_degraded_path(stub_llm_factory):
    canned_resp = {
        "response": (
            "Here are your events, but note that Gmail search failed so some data "
            "might be missing.\n✓ Searched calendar."
        ),
        "actions_taken": [
            {
                "tool": "gcal.search_events",
                "args": {},
                "result": "Found 1 event",
                "status": "executed",
            }
        ],
    }
    stub_llm_factory(json.dumps(canned_resp))

    intent = Intent(services=["gmail", "gcal"], intent="Check schedule")
    node_outputs = {
        "$n1": {"events": ["Meeting at 2"]},
        "$n2": {"error": "Gmail service is down"},
    }

    result = await synthesize(
        intent, node_outputs, pending_actions=[], llm_client=llm_client
    )

    assert isinstance(result, TaskResult)
    assert "Gmail search failed" in result.response
    assert len(result.actions_taken) == 1
