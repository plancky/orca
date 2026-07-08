import datetime

from backend.llm.client import llm_client as default_llm_client
from backend.llm.json_utils import _ChatClient, extract_and_validate
from backend.llm.prompts.planner import build_planner_prompt
from backend.orchestration.models.dag import Plan
from backend.orchestration.models.intent import Intent
from backend.orchestration.utils.tools import REGISTRY


class PlannerError(Exception):
    pass


class HallucinatedToolError(PlannerError):
    pass


async def plan(
    intent: Intent,
    tool_catalog: dict[str, str] | None = None,
    llm_client: _ChatClient | None = None,
    frozen_now: datetime.datetime | None = None,
    tz: str | None = None,
) -> Plan:
    client = llm_client or default_llm_client
    if tool_catalog is None:
        tool_catalog = {
            name: (fn.__doc__ or "").strip().split("\n")[0]
            for name, fn in REGISTRY.items()
        }

    prompt = build_planner_prompt(intent, tool_catalog, frozen_now, tz)
    messages = [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": prompt["user"]},
    ]
    raw = await client.chat(messages, response_format="json_object")
    plan_obj = await extract_and_validate(
        raw, Plan, llm_client=client, schema_name="Plan"
    )

    for node in plan_obj.nodes:
        if node.tool not in REGISTRY:
            raise HallucinatedToolError(f"Hallucinated tool: {node.tool}")

    return plan_obj
