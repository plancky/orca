import datetime
from zoneinfo import ZoneInfo

from backend.orchestration.models.intent import Intent


def build_planner_prompt(
    intent: Intent,
    tool_catalog: dict[str, str],
    frozen_now: datetime.datetime | None = None,
    tz: str | None = None,
) -> dict[str, str]:
    now = frozen_now or datetime.datetime.now(ZoneInfo(tz or "UTC"))

    catalog_lines = []
    for tool_name, desc in tool_catalog.items():
        catalog_lines.append(f"- {tool_name}: {desc}")
    catalog_str = "\n".join(catalog_lines)

    system = f"""You are an expert Query Planner orchestrating tasks
across Google Workspace services.
Current Datetime: {now.isoformat()}
User Timezone: {now.tzinfo}

You have access to the following tools:
{catalog_str}

Your job is to read the user's intent and produce a DAG
(Directed Acyclic Graph) of operations required to fulfill it.

OUTPUT FORMAT:
Return ONLY a valid JSON object matching this schema:
{{
  "nodes": [
    {{
      "id": "n1", 
      "tool": "tool_name_here", 
      "args": {{"key": "value"}}, 
      "depends_on": [], 
      "optional": false
    }}
  ]
}}

RULES:
- independent nodes carry `depends_on: []`
- chained nodes carry `depends_on: ["nX"]` where "nX" is the upstream node ID
- deferred args use `"$nX.field"` (e.g. `"$n1.booking_ref"`)
- USE ONLY TOOLS FROM THE PROVIDED CATALOG. Do NOT hallucinate tools.
"""

    user = f"""INTENT: {intent.model_dump_json(indent=2)}
PLAN:"""

    return {"system": system, "user": user}
