import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import CurrentUser, get_session
from backend.db.models import Task, TaskPublic

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get(
    "/{task_id}",
    response_model=TaskPublic,
)
async def get_task(
    task_id: uuid.UUID,
    user: CurrentUser,
    session: AsyncSession = Depends(get_session),
):
    task = await session.get(Task, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )
    return task
