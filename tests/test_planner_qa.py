from pathlib import Path

import pytest

from backend.agents import WRITE_TOOLS
from backend.orchestration.models.dag import Plan
from backend.orchestration.models.intent import Intent
from backend.orchestration.stages.planner import HallucinatedToolError, plan

FIXTURE_DIR = (
    Path(__file__).parent.parent
    / "backend"
    / "orchestration"
    / "stages"
    / "tests"
    / "fixtures"
    / "llm"
    / "planner"
)


def load_fixture(name: str) -> str:
    return (FIXTURE_DIR / f"{name}.json").read_text()


def assert_dag(
    plan_obj: Plan,
    expected_nodes: int,
    expected_tools: set[str],
    expected_parallel_layers: int,
    expected_deferred_refs: int,
):
    assert len(plan_obj.nodes) == expected_nodes
    assert {n.tool for n in plan_obj.nodes} == expected_tools

    # Calculate parallel layers (longest path)
    layers = {}

    def get_layer(node_id):
        if node_id in layers:
            return layers[node_id]

        node = next((n for n in plan_obj.nodes if n.id == node_id), None)
        if not node or not node.depends_on:
            layers[node_id] = 1
            return 1

        layer = 1 + max(get_layer(dep) for dep in node.depends_on)
        layers[node_id] = layer
        return layer

    for n in plan_obj.nodes:
        get_layer(n.id)

    actual_layers = max(layers.values()) if layers else 0
    assert actual_layers == expected_parallel_layers

    # Count deferred refs
    deferred_refs = 0
    for n in plan_obj.nodes:
        for val in n.args.values():
            if isinstance(val, str) and val.startswith("$n"):
                deferred_refs += 1

    assert deferred_refs == expected_deferred_refs


@pytest.mark.asyncio
async def test_calendar_read(stub_llm_factory):
    stub_llm_factory(load_fixture("calendar-read"))
    intent = Intent(services=["gcal"], intent="Read calendar", entities={})
    p = await plan(intent)
    assert_dag(p, 1, {"gcal.search_events"}, 1, 0)


@pytest.mark.asyncio
async def test_gmail_search(stub_llm_factory):
    stub_llm_factory(load_fixture("gmail-search"))
    intent = Intent(services=["gmail"], intent="Search emails", entities={})
    p = await plan(intent)
    assert_dag(p, 1, {"gmail.search_emails"}, 1, 0)
    assert p.nodes[0].args["sender"] == "boss@example.com"


@pytest.mark.asyncio
async def test_prepare_meeting(stub_llm_factory):
    stub_llm_factory(load_fixture("prepare-meeting"))
    intent = Intent(
        services=["gcal", "gmail", "drive"], intent="Prepare for meeting", entities={}
    )
    p = await plan(intent)
    assert_dag(
        p, 3, {"gcal.search_events", "gmail.search_emails", "drive.search_files"}, 1, 0
    )


@pytest.mark.asyncio
async def test_cancel_flight(stub_llm_factory):
    stub_llm_factory(load_fixture("cancel-flight"))
    intent = Intent(services=["gmail", "gcal"], intent="Cancel flight", entities={})
    p = await plan(intent)
    assert_dag(
        p, 3, {"gmail.search_emails", "gcal.search_events", "gcal.delete_event"}, 3, 2
    )
    # Check write-gated node
    write_node = next(n for n in p.nodes if n.tool in WRITE_TOOLS)
    assert write_node.tool == "gcal.delete_event"


@pytest.mark.asyncio
async def test_conflict_ooo(stub_llm_factory):
    stub_llm_factory(load_fixture("conflict-ooo"))
    intent = Intent(
        services=["drive", "gcal"], intent="Check OOO conflict", entities={}
    )
    p = await plan(intent)
    assert_dag(
        p, 3, {"drive.search_files", "gcal.search_events", "conflict.detect"}, 2, 2
    )


@pytest.mark.asyncio
async def test_malformed_repair(stub_llm_factory):
    calls = []

    def fake_response(messages):
        calls.append(messages)
        if len(calls) == 1:
            return load_fixture("malformed")
        return load_fixture("malformed-repaired")

    stub_llm_factory(fake_response)
    intent = Intent(services=["gmail"], intent="Malformed test", entities={})
    p = await plan(intent)
    assert_dag(p, 1, {"gmail.search_emails"}, 1, 0)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_non_registry_tool(stub_llm_factory):
    stub_llm_factory(load_fixture("non-registry-tool"))
    intent = Intent(services=["hallucinated"], intent="Hallucination test", entities={})
    with pytest.raises(
        HallucinatedToolError, match="Hallucinated tool: hallucinated.tool"
    ):
        await plan(intent)
