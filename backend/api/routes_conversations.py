"""Conversation history endpoints (frontend left-panel + thread hydration).

User-scoped reads over the existing ``conversation``/``message`` tables — the
persistence SoR the orchestrator already writes on every turn (see
``backend/context/conversation.py``). Reuses the existing
``ConversationPublic`` / ``ConversationWithMessages`` / ``MessagePublic``
response models; no new tables. ``GET /`` lists the caller's conversations
(newest activity first); ``GET /{id}`` returns one conversation's ordered turns
(404 on a missing or non-owned id — the same ownership guard as
``routes_tasks``).
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

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
