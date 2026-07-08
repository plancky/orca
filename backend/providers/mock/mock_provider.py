"""``MockProvider`` — the offline/dev data plane behind the frozen ``Provider``.

Reads the seeded corpus (``seed_corpus.py``) through the same hybrid pgvector
search the real provider would use, and records writes to ``actions_log`` WITHOUT
ever mutating the corpus — the executor's write-gate means ``execute`` only fires
on a user-confirmed action (PLAN.md l.538-541).

Session + ``user_id`` are carried as instance state because the frozen
``Provider`` ABC (``search``/``get``/``execute``) takes neither — the factory or a
tool binds them per request (``get_provider(session, user_id)``). Query vectors
use ``FakeEmbedder`` so ranking matches the fake vectors ``seed_corpus`` wrote,
fully offline (no model server, no quota).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel

from backend.agents.base import Provider
from backend.db.models import (
    ActionsLog,
    ActionStatus,
    GCalDatasource,
    GDriveDatasource,
    GmailDatasource,
)
from backend.embeddings.search import hybrid_search
from backend.testing.fakes import FakeEmbedder

# service token -> (datasource model, business-id column). "drive"/"gdrive" and
# "gcal"/"calendar" are accepted aliases so planner/agent naming stays flexible.
_DATASOURCE: dict[str, tuple[type[SQLModel], str]] = {
    "gmail": (GmailDatasource, "email_id"),
    "gcal": (GCalDatasource, "event_id"),
    "calendar": (GCalDatasource, "event_id"),
    "drive": (GDriveDatasource, "file_id"),
    "gdrive": (GDriveDatasource, "file_id"),
}


def _row_to_dict(row: SQLModel) -> dict[str, Any]:
    """Flatten a datasource row to its column values (no relationships)."""
    return {col.name: getattr(row, col.name) for col in row.__table__.columns}  # type: ignore[attr-defined]


class MockProvider(Provider):
    def __init__(
        self,
        session: AsyncSession | None = None,
        user_id: str | uuid.UUID | None = None,
    ) -> None:
        self.session = session
        self.user_id = str(user_id) if user_id is not None else None

    def _require_session(self) -> AsyncSession:
        if self.session is None:
            raise RuntimeError("MockProvider has no bound session")
        return self.session

    def _require_user(self) -> uuid.UUID:
        if self.user_id is None:
            raise RuntimeError("MockProvider has no bound user_id")
        return uuid.UUID(self.user_id)

    async def search(
        self, service: str, query: str, filters: dict
    ) -> list[dict]:
        filters = filters or {}
        top_k = int(filters.get("_top_k", 10))
        metadata = {
            k: v
            for k, v in filters.items()
            if not k.startswith("_") and k not in ("session", "user_id")
        }
        q_embedding = await FakeEmbedder().embed_query(query, user_id=self.user_id)
        return await hybrid_search(
            self._require_session(),
            q_embedding,
            service,
            str(self._require_user()),
            filters=metadata or None,
            top_k=top_k,
        )

    async def get(self, service: str, item_id: str) -> dict:
        model, business_id = _DATASOURCE[service]
        session = self._require_session()
        stmt = select(model).where(model.user_id == self._require_user())  # type: ignore[attr-defined]
        parsed = _maybe_uuid(item_id)
        stmt = stmt.where(
            model.id == parsed  # type: ignore[attr-defined]
            if parsed is not None
            else getattr(model, business_id) == item_id
        )
        row = (await session.execute(stmt)).scalars().first()
        return _row_to_dict(row) if row is not None else {}

    async def execute(self, service: str, action: str, args: dict) -> dict:
        """Record a (simulated) write to ``actions_log`` — never touch corpus.

        Default ``status=simulated``; a confirmed write (executor resume path)
        passes ``args["_confirmed"]=True`` to mark it ``executed``. Control keys
        (``_``-prefixed) are stripped before the args are persisted.
        """
        args = args or {}
        confirmed = bool(args.get("_confirmed", False))
        status = (
            ActionStatus.EXECUTED.value
            if confirmed
            else ActionStatus.SIMULATED.value
        )
        clean_args = {k: v for k, v in args.items() if not k.startswith("_")}
        session = self._require_session()
        row = ActionsLog(
            user_id=self._require_user(),
            tool=f"{service}.{action}",
            args=clean_args,
            status=status,
            result={"simulated": not confirmed},
        )
        session.add(row)
        await session.flush()
        return {
            "action_id": str(row.id),
            "tool": row.tool,
            "status": status,
            "args": clean_args,
        }


def _maybe_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None
