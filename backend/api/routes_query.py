import functools
import uuid
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import CurrentUser, enforce_rate_limit, get_session
from backend.db.models import ActionsLog, Conversation, Task, TaskKind, TaskStatus
from backend.orchestration.utils.checkpoint import get_checkpoint_for_action
from backend.workers.confirm import run_resume
from backend.workers.orchestrate import run_pipeline

router = APIRouter(prefix="/query", tags=["query"])


class ConfirmAction(BaseModel):
    action_id: uuid.UUID
    decision: str


class QueryRequest(BaseModel):
    query: str
    conversation_id: uuid.UUID | None = None
    confirm: ConfirmAction | None = None


class QueryResponse(BaseModel):
    task_id: uuid.UUID
    status: str
    conversation_id: uuid.UUID


_QUERY_EXAMPLES = {
    "single_service": {
        "summary": "Single service — routes to one agent",
        "value": {"query": "Find emails from sarah@company.com about the budget"},
    },
    "multi_service": {
        "summary": "Multi service — parallel reads across agents",
        "value": {"query": "Prepare for tomorrow's meeting with Acme Corp"},
    },
    "hard_ambiguous": {
        "summary": "Hard case — ambiguity triggers a clarification",
        "value": {"query": "Move the meeting with John"},
    },
    "confirm_action": {
        "summary": "Confirm a write-gated action (resume from checkpoint)",
        "value": {
            "query": "Yes, send it.",
            "conversation_id": "00000000-0000-0000-0000-000000000000",
            "confirm": {
                "action_id": "00000000-0000-0000-0000-000000000000",
                "decision": "approved",
            },
        },
    },
}


@router.post(
    "",
    response_model=QueryResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(enforce_rate_limit)],
)
async def submit_query(
    req: Annotated[QueryRequest, Body(openapi_examples=_QUERY_EXAMPLES)],
    user: CurrentUser,
    session: AsyncSession = Depends(get_session),
) -> Any:
    u_id = user.id
    if req.confirm:
        cp = await get_checkpoint_for_action(session, req.confirm.action_id)
        if not cp:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Checkpoint not found for action.",
            )

        log_row = await session.get(ActionsLog, req.confirm.action_id)
        parent_task_id = log_row.task_id if log_row else None

        conv_id = req.conversation_id
        if not conv_id:
            if log_row and log_row.conversation_id:
                conv_id = log_row.conversation_id
            else:
                title = req.query[:80] if req.query else "Confirmation"
                conv = Conversation(user_id=u_id, title=title)
                session.add(conv)
                await session.flush()
                conv_id = conv.id

        task = Task(
            user_id=u_id,
            conversation_id=conv_id,
            kind=TaskKind.CONFIRM.value,
            status=TaskStatus.QUEUED.value,
            parent_task_id=parent_task_id,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

        await anyio.to_thread.run_sync(
            functools.partial(
                run_resume.delay,
                cp.dump(),
                req.confirm.decision,
                str(task.id),
                str(u_id),
            )
        )

        return QueryResponse(
            task_id=task.id,
            status=task.status,
            conversation_id=conv_id,
        )

    conv_id = req.conversation_id
    if not conv_id:
        title = req.query[:80] if req.query else "New Conversation"
        conv = Conversation(user_id=u_id, title=title)
        session.add(conv)
        await session.flush()
        conv_id = conv.id

    task = Task(
        user_id=u_id,
        conversation_id=conv_id,
        kind=TaskKind.QUERY.value,
        status=TaskStatus.QUEUED.value,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    await anyio.to_thread.run_sync(
        functools.partial(
            run_pipeline.delay,
            str(task.id),
            str(u_id),
            req.query,
            str(conv_id),
        )
    )

    return QueryResponse(
        task_id=task.id,
        status=task.status,
        conversation_id=conv_id,
    )
