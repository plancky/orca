"""Gmail agent + its registered tools.

Every ``@register_tool`` here populates the Wave-0 ``REGISTRY`` on import. A tool
is ``async (session, user_id, args: dict) -> dict | list[dict]`` — the executor
(Wave C) resolves a node's args, then calls the tool by its ``"gmail.<action>"``
key. Write tools (``send_email``/``update_labels``) appear in
``backend.agents.WRITE_TOOLS`` so the executor gates them before calling.
"""

from backend.agents.base import BaseAgent
from backend.orchestration.utils.tools import register_tool


class GmailAgent(BaseAgent):
    service = "gmail"

    async def search(
        self, query: str, filters: dict | None = None
    ) -> list[dict]:
        return await self.provider.search(self.service, query, filters or {})

    async def get_context(self, item_id: str) -> dict:
        return await self.provider.get(self.service, item_id)

    async def execute(self, action: str, args: dict) -> dict:
        return await self.provider.execute(self.service, action, args)


def _gmail(session, user_id) -> GmailAgent:
    # Lazy import avoids the agents<->providers import cycle.
    from backend.providers.factory import get_provider

    return GmailAgent(get_provider(session=session, user_id=user_id))


@register_tool("gmail.search_emails")
async def search_emails(session, user_id, args: dict) -> list[dict]:
    return await _gmail(session, user_id).search(
        args.get("query", ""), args.get("filters")
    )


@register_tool("gmail.get_email")
async def get_email(session, user_id, args: dict) -> dict:
    return await _gmail(session, user_id).get_context(
        args.get("id") or args.get("email_id") or ""
    )


@register_tool("gmail.send_email")
async def send_email(session, user_id, args: dict) -> dict:
    return await _gmail(session, user_id).execute("send_email", args)


@register_tool("gmail.draft_email")
async def draft_email(session, user_id, args: dict) -> dict:
    return await _gmail(session, user_id).execute("draft_email", args)


@register_tool("gmail.update_labels")
async def update_labels(session, user_id, args: dict) -> dict:
    return await _gmail(session, user_id).execute("update_labels", args)
