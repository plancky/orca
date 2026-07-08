"""Planner JSON-contract suite (Intent -> Plan DAG).

Tier 1 (default, ``-m "not llm"``): the LLM is stubbed with recorded fixtures;
assertions are purely structural — node count, the ``tool`` set, the
``depends_on`` topology (parallel vs sequential), deferred ``$nX.field`` refs,
and the registry guard (every ``tool`` must live in ``REGISTRY``; a
non-registry fixture raises ``HallucinatedToolError``). Tier 2 (``-m llm``)
runs the real planner and checks the same invariants; skipped without a key.
"""

import pytest

from backend.agents import WRITE_TOOLS  # import side effect: populates REGISTRY
from backend.config import settings
from backend.orchestration.models.dag import DEFERRED_REF_RE, Node, Plan
from backend.orchestration.models.intent import Intent
from backend.orchestration.stages.planner import HallucinatedToolError, plan
from backend.orchestration.utils.tools import REGISTRY

requires_llm_key = pytest.mark.skipif(
    not settings.GEMINI_STUDIO_API_KEY,
    reason="Tier-2 llm tests need GEMINI_STUDIO_API_KEY + live INFERENCE_BASE_URL",
)


def _layer_depth(plan_obj: Plan) -> int:
    """Longest dependency chain (1 = all parallel, N = N sequential layers)."""
    by_id = {n.id: n for n in plan_obj.nodes}
    memo: dict[str, int] = {}

    def depth(node_id: str) -> int:
        if node_id in memo:
            return memo[node_id]
        node = by_id.get(node_id)
        memo[node_id] = (
            1
            if node is None or not node.depends_on
            else 1 + max(depth(d) for d in node.depends_on)
        )
        return memo[node_id]

    return max((depth(n.id) for n in plan_obj.nodes), default=0)


def _count_deferred_refs(plan_obj: Plan) -> int:
    return sum(
        1
        for n in plan_obj.nodes
        for v in n.args.values()
        if isinstance(v, str) and DEFERRED_REF_RE.match(v)
    )


def assert_dag(
    plan_obj: Plan,
    *,
    nodes: int,
    tools: set[str],
    layers: int,
    deferred_refs: int,
) -> None:
    assert len(plan_obj.nodes) == nodes
    assert {n.tool for n in plan_obj.nodes} == tools
    assert all(n.tool in REGISTRY for n in plan_obj.nodes)  # registry guard
    assert _layer_depth(plan_obj) == layers
    assert _count_deferred_refs(plan_obj) == deferred_refs


def _node(plan_obj: Plan, node_id: str) -> Node:
    return next(n for n in plan_obj.nodes if n.id == node_id)


def test_registry_is_populated():
    """Importing backend.agents wires all 15 agent tools + conflict.detect."""
    assert len(REGISTRY) == 16
    assert "conflict.detect" in REGISTRY
    assert WRITE_TOOLS <= set(REGISTRY)


@pytest.mark.asyncio
async def test_planner_calendar_read_single_node(stub_llm):
    stub_llm.planner("calendar-read")
    p = await plan(Intent(services=["gcal"], intent="read calendar"))
    assert_dag(p, nodes=1, tools={"gcal.search_events"}, layers=1, deferred_refs=0)
    assert p.nodes[0].depends_on == []


@pytest.mark.asyncio
async def test_planner_gmail_search_pushes_filters(stub_llm):
    stub_llm.planner("gmail-search")
    p = await plan(Intent(services=["gmail"], intent="search emails"))
    assert_dag(p, nodes=1, tools={"gmail.search_emails"}, layers=1, deferred_refs=0)
    assert p.nodes[0].args["sender"] == "boss@example.com"


@pytest.mark.asyncio
async def test_planner_prepare_meeting_is_one_parallel_layer(stub_llm):
    stub_llm.planner("prepare-meeting")
    p = await plan(
        Intent(services=["gcal", "gmail", "drive"], intent="prepare for meeting")
    )
    assert_dag(
        p,
        nodes=3,
        tools={"gcal.search_events", "gmail.search_emails", "drive.search_files"},
        layers=1,
        deferred_refs=0,
    )
    assert all(n.depends_on == [] for n in p.nodes)  # no ordering imposed


@pytest.mark.asyncio
async def test_planner_cancel_flight_is_sequential_write_gated(stub_llm):
    stub_llm.planner("cancel-flight")
    p = await plan(Intent(services=["gmail", "gcal"], intent="cancel flight"))
    assert_dag(
        p,
        nodes=3,
        tools={"gmail.search_emails", "gcal.search_events", "gcal.delete_event"},
        layers=3,
        deferred_refs=2,
    )
    # sequential chain n1 -> n2 -> n3
    assert _node(p, "n2").depends_on == ["n1"]
    assert _node(p, "n3").depends_on == ["n2"]
    # write-gated terminal node the executor will suspend on; deferred ref upstream
    assert _node(p, "n3").tool in WRITE_TOOLS
    assert _node(p, "n2").args["query"] == "$n1.booking_ref"


@pytest.mark.asyncio
async def test_planner_conflict_ooo_is_fan_in(stub_llm):
    stub_llm.planner("conflict-ooo")
    p = await plan(Intent(services=["drive", "gcal"], intent="detect conflict"))
    assert_dag(
        p,
        nodes=3,
        tools={"drive.search_files", "gcal.search_events", "conflict.detect"},
        layers=2,
        deferred_refs=2,
    )
    # two parallel reads fan into the conflict node
    assert _node(p, "n1").depends_on == []
    assert _node(p, "n2").depends_on == []
    assert set(_node(p, "n3").depends_on) == {"n1", "n2"}


@pytest.mark.asyncio
async def test_planner_malformed_triggers_single_repair(stub_llm):
    stub_llm.sequence("planner", ["malformed", "malformed-repaired"])
    p = await plan(Intent(services=["gmail"], intent="malformed"))
    assert_dag(p, nodes=1, tools={"gmail.search_emails"}, layers=1, deferred_refs=0)
    assert len(stub_llm.calls) == 2  # initial + exactly one repair


@pytest.mark.asyncio
async def test_planner_registry_guard_rejects_hallucinated_tool(stub_llm):
    stub_llm.planner("non-registry-tool")
    with pytest.raises(HallucinatedToolError, match="hallucinated.tool"):
        await plan(Intent(services=["x"], intent="hallucinate"))


# --- Tier 2: live planner, structural invariants only (skipped by default) ---

_LIVE_CASES = (
    (Intent(services=["gcal"], intent="read calendar"), {"gcal.search_events"}, 1),
    (
        Intent(services=["gcal", "gmail", "drive"], intent="prepare for meeting"),
        {"gcal.search_events", "gmail.search_emails", "drive.search_files"},
        1,
    ),
)


@pytest.mark.llm
@requires_llm_key
@pytest.mark.asyncio
@pytest.mark.parametrize(("intent", "expected_tools", "layers"), _LIVE_CASES)
async def test_planner_live_contract(intent, expected_tools, layers):
    p = await plan(intent)
    assert {n.tool for n in p.nodes} <= set(REGISTRY)  # registry guard holds live
    assert {n.tool for n in p.nodes} == expected_tools
    assert _layer_depth(p) == layers
