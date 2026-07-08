"""Google Calendar agent + its registered tools.

Every ``@register_tool`` here populates the Wave-0 ``REGISTRY`` on import. A tool
is ``async (session, user_id, args: dict) -> dict | list[dict]`` — the executor
(Wave C) resolves a node's args, then calls the tool by its ``"gcal.<action>"``
key. Write tools (``create_event``/``update_event``/``delete_event``) appear in
``backend.agents.WRITE_TOOLS`` so the executor gates them before calling.
"""

from backend.agents.base import BaseAgent
from backend.orchestration.utils.tools import register_tool


class GCalAgent(BaseAgent):
    service = "gcal"

    async def search(
        self, query: str, filters: dict | None = None
    ) -> list[dict]:
        return await self.provider.search(self.service, query, filters or {})

    async def get_context(self, item_id: str) -> dict:
        return await self.provider.get(self.service, item_id)

    async def execute(self, action: str, args: dict) -> dict:
        return await self.provider.execute(self.service, action, args)


def _gcal(session, user_id) -> GCalAgent:
    # Lazy import avoids the agents<->providers import cycle.
    from backend.providers.factory import get_provider

    return GCalAgent(get_provider(session=session, user_id=user_id))


@register_tool("gcal.search_events")
async def search_events(session, user_id, args: dict) -> list[dict]:
    return await _gcal(session, user_id).search(
        args.get("query", ""), args.get("filters")
    )


@register_tool("gcal.get_event")
async def get_event(session, user_id, args: dict) -> dict:
    return await _gcal(session, user_id).get_context(
        args.get("id") or args.get("event_id") or ""
    )


@register_tool("gcal.create_event")
async def create_event(session, user_id, args: dict) -> dict:
    return await _gcal(session, user_id).execute("create_event", args)


@register_tool("gcal.update_event")
async def update_event(session, user_id, args: dict) -> dict:
    return await _gcal(session, user_id).execute("update_event", args)


@register_tool("gcal.delete_event")
async def delete_event(session, user_id, args: dict) -> dict:
    return await _gcal(session, user_id).execute("delete_event", args)
