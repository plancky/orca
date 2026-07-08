from fastapi import APIRouter
from sqlalchemy import select

from backend.api.deps import CurrentUser, SessionDep
from backend.db.models import SyncStatus
from backend.workers.sync import sync_all_users

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/trigger")
async def trigger_sync(user: CurrentUser) -> dict:
    """Enqueue sync for all users."""
    sync_all_users.delay()
    return {"status": "enqueued"}


@router.get("/status")
async def get_status(user: CurrentUser, session: SessionDep) -> list[dict]:
    """Return per-service sync status."""
    statuses = (
        await session.execute(
            select(SyncStatus).where(SyncStatus.user_id == user.id)
        )
    ).scalars().all()
    return [
        {
            "service": s.service,
            "last_synced_at": s.last_synced_at,
            "item_count": s.item_count,
        }
        for s in statuses
    ]
