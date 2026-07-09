"""Conversation history endpoints (frontend left-panel + thread hydration).

User-scoped reads over the existing ``conversation``/``message`` tables — the
persistence SoR the orchestrator already writes on every turn (see
``backend/context/conversation.py``). Reuses the existing
``ConversationPublic`` / ``ConversationWithMessages`` / ``MessagePublic``
response models; no new tables. ``GET /`` lists the caller's conversations
(newest activity first); ``GET /{id}`` returns one conversation's ordered turns
(404 on a missing or non-owned id — the same ownership guard as
``routes_tasks``). ``DELETE /{id}`` removes a conversation; ``Message`` (and
``Task``) rows FK to ``conversation.id`` with ``ondelete="CASCADE"``, so the
database cleans up dependent rows — no manual cascade needed here.
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select

from backend.api.deps import CurrentUser, SessionDep
from backend.db.models import (
    Conversation,
    ConversationPublic,
    ConversationWithMessages,
    Message,
    MessagePublic,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationPublic])
async def list_conversations(user: CurrentUser, session: SessionDep):
    """List the caller's conversations, newest activity first."""
    rows = (
        (
            await session.execute(
                select(Conversation)
                .where(Conversation.user_id == user.id)
                .order_by(Conversation.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.get("/{conversation_id}", response_model=ConversationWithMessages)
async def get_conversation(
    conversation_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationWithMessages:
    """Return one conversation's ordered turns (404 if missing or not owned)."""
    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    messages = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.seq)
            )
        )
        .scalars()
        .all()
    )
    return ConversationWithMessages(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=[MessagePublic.model_validate(m) for m in messages],
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
) -> None:
    """Delete a conversation (404 if missing or not owned).

    Issued as a Core ``DELETE`` (not ``session.delete(conv)``): the ORM's
    default unit-of-work would otherwise try to null out the loaded
    ``messages`` collection's ``conversation_id`` before deleting the parent,
    which violates that column's ``NOT NULL`` constraint. A bare ``DELETE``
    lets Postgres's own ``ON DELETE CASCADE`` (see ``backend/db/models.py``)
    remove the dependent ``message``/``task`` rows instead.
    """
    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    await session.execute(
        delete(Conversation).where(Conversation.id == conversation_id)
    )
    await session.commit()
