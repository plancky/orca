"""Google Drive agent + its registered tools.

Every ``@register_tool`` here populates the Wave-0 ``REGISTRY`` on import. A tool
is ``async (session, user_id, args: dict) -> dict | list[dict]`` — the executor
(Wave C) resolves a node's args, then calls the tool by its ``"drive.<action>"``
key. Write tools (``share_file``/``move_file``) appear in
``backend.agents.WRITE_TOOLS`` so the executor gates them before calling.
"""

from backend.agents.base import BaseAgent
from backend.orchestration.utils.tools import register_tool


class DriveAgent(BaseAgent):
    service = "drive"

    async def search(
        self, query: str, filters: dict | None = None
    ) -> list[dict]:
        return await self.provider.search(self.service, query, filters or {})

    async def get_context(self, item_id: str) -> dict:
        return await self.provider.get(self.service, item_id)

    async def execute(self, action: str, args: dict) -> dict:
        return await self.provider.execute(self.service, action, args)


def _drive(session, user_id) -> DriveAgent:
    # Lazy import avoids the agents<->providers import cycle.
    from backend.providers.factory import get_provider

    return DriveAgent(get_provider(session=session, user_id=user_id))


@register_tool("drive.search_files")
async def search_files(session, user_id, args: dict) -> list[dict]:
    return await _drive(session, user_id).search(
        args.get("query", ""), args.get("filters")
    )


@register_tool("drive.get_file")
async def get_file(session, user_id, args: dict) -> dict:
    return await _drive(session, user_id).get_context(
        args.get("id") or args.get("file_id") or ""
    )


@register_tool("drive.share_file")
async def share_file(session, user_id, args: dict) -> dict:
    return await _drive(session, user_id).execute("share_file", args)


@register_tool("drive.create_folder")
async def create_folder(session, user_id, args: dict) -> dict:
    return await _drive(session, user_id).execute("create_folder", args)


@register_tool("drive.move_file")
async def move_file(session, user_id, args: dict) -> dict:
    return await _drive(session, user_id).execute("move_file", args)
