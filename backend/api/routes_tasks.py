import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import CurrentUser, get_session
from backend.db.models import Task, TaskPublic

router = APIRouter(prefix="/tasks", tags=["tasks"])

_TID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
_CID = "9b2c1f10-0000-4000-8000-000000000000"

_LIFECYCLE_EXAMPLES = {
    "queued": {
        "summary": "Just enqueued",
        "value": {
            "id": _TID,
            "kind": "query",
            "status": "queued",
            "conversation_id": _CID,
            "progress": None,
            "result": None,
            "error": None,
            "parent_task_id": None,
        },
    },
    "running": {
        "summary": "Pipeline in flight",
        "value": {
            "id": _TID,
            "kind": "query",
            "status": "running",
            "conversation_id": _CID,
            "progress": {"node": "gmail.search_emails"},
            "result": None,
        },
    },
    "awaiting_confirmation": {
        "summary": "Suspended on a write gate",
        "value": {
            "id": _TID,
            "kind": "query",
            "status": "awaiting_confirmation",
            "conversation_id": _CID,
            "result": {
                "response": "I drafted a cancellation. Send it?",
                "actions_taken": [],
                "pending_actions": [
                    {
                        "action_id": "b1e5a7c2-0000-4000-8000-000000000000",
                        "tool": "gmail.send_email",
                        "args": {"to": "support@turkishairlines.com"},
                        "preview": "Draft: cancellation for PNR TK4471",
                    }
                ],
            },
        },
    },
    "success": {
        "summary": "Completed",
        "value": {
            "id": _TID,
            "kind": "query",
            "status": "success",
            "conversation_id": _CID,
            "result": {
                "response": "I found 2 emails from sarah@company.com "
                "about the budget.",
                "actions_taken": [
                    {"tool": "gmail.search_emails", "status": "executed"}
                ],
                "pending_actions": None,
            },
        },
    },
}


@router.get(
    "/{task_id}",
    response_model=TaskPublic,
    responses={
        200: {
            "description": "Task lifecycle snapshot; poll until terminal.",
            "content": {"application/json": {"examples": _LIFECYCLE_EXAMPLES}},
        }
    },
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
