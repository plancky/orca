"""Phase-2 Google Workspace provider — real implementation behind ``Provider``.

``search`` stays cache-backed: it embeds the query with the real ``embedder``
(Modal BGE / Gemini per config) and delegates to ``hybrid_search`` over the
``*_vector_store`` chunks — no Google call, identical to ``MockProvider``. Only
``get`` (live full-content fetch) and ``execute`` (writes) touch Google, through
the per-service adapters (``gmail`` / ``gcal`` / ``drive``).

The write gate lives upstream in the executor (``WRITE_TOOLS`` suspends before
``execute`` is called); here ``execute`` is the confirmed branch and honors
``DRY_RUN_WRITES`` (simulate + log, no mutation). Every ``googleapiclient`` import
is lazy so the mock/offline path and the CPU-only image never load it.
"""

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.base import Provider
from backend.config import settings
from backend.db.models import ActionsLog, ActionStatus
from backend.embeddings.embedder import embedder
from backend.embeddings.search import hybrid_search
from backend.providers.google import drive, gcal, gmail

_ADAPTERS = {"gmail": gmail, "gcal": gcal, "gdrive": drive}
_ALIASES = {"drive": "gdrive", "calendar": "gcal"}


def normalize_service(service: str) -> str:
    """Map an agent token to the canonical ``gmail`` / ``gcal`` / ``gdrive``."""
    return _ALIASES.get(service, service)


def adapter_for(service: str):
    return _ADAPTERS[normalize_service(service)]


async def build_service(session, user_id, name: str, version: str):
    """Build a per-service googleapiclient resource with refreshed credentials."""
    from backend.providers.google.credentials import credentials_for

    creds = await credentials_for(session, user_id)

    def _build():
        from googleapiclient.discovery import build

        return build(name, version, credentials=creds, cache_discovery=False)

    return await asyncio.to_thread(_build)


class GoogleProvider(Provider):
    def __init__(
        self,
        session: AsyncSession | None = None,
        user_id: str | uuid.UUID | None = None,
    ) -> None:
        self.session = session
        self.user_id = str(user_id) if user_id is not None else None

    def _require_session(self) -> AsyncSession:
        if self.session is None:
            raise RuntimeError("GoogleProvider has no bound session")
        return self.session

    def _require_user(self) -> uuid.UUID:
        if self.user_id is None:
            raise RuntimeError("GoogleProvider has no bound user_id")
        return uuid.UUID(self.user_id)

    async def search(self, service: str, query: str, filters: dict) -> list[dict]:
        filters = filters or {}
        top_k = int(filters.get("_top_k", 10))
        metadata = {
            k: v
            for k, v in filters.items()
            if not k.startswith("_") and k not in ("session", "user_id")
        }
        q_embedding = await embedder.embed_query(query, user_id=self.user_id)
        return await hybrid_search(
            self._require_session(),
            q_embedding,
            normalize_service(service),
            str(self._require_user()),
            filters=metadata or None,
            top_k=top_k,
        )

    async def get(self, service: str, item_id: str) -> dict:
        adapter = adapter_for(service)
        client = await build_service(
            self._require_session(),
            self._require_user(),
            adapter.SERVICE_NAME,
            adapter.SERVICE_VERSION,
        )
        return await asyncio.to_thread(adapter.get_full, client, item_id)

    async def execute(self, service: str, action: str, args: dict) -> dict:
        args = args or {}
        clean_args = {k: v for k, v in args.items() if not k.startswith("_")}

        if settings.DRY_RUN_WRITES:
            return await self._record(
                service, action, clean_args, ActionStatus.SIMULATED, {"dry_run": True}
            )

        adapter = adapter_for(service)
        client = await build_service(
            self._require_session(),
            self._require_user(),
            adapter.SERVICE_NAME,
            adapter.SERVICE_VERSION,
        )
        try:
            result = await asyncio.to_thread(adapter.write, client, action, clean_args)
        except Exception as exc:
            await self._record(
                service, action, clean_args, ActionStatus.FAILED, {"error": str(exc)}
            )
            raise
        return await self._record(
            service, action, clean_args, ActionStatus.EXECUTED, result
        )

    async def _record(
        self, service: str, action: str, args: dict, status: ActionStatus, result: dict
    ) -> dict:
        session = self._require_session()
        row = ActionsLog(
            user_id=self._require_user(),
            tool=f"{service}.{action}",
            args=args,
            status=status.value,
            result=result,
        )
        session.add(row)
        await session.flush()
        return {
            "action_id": str(row.id),
            "tool": row.tool,
            "status": status.value,
            "args": args,
            "result": result,
        }
