import uuid

from backend.db.models import Message, MessageRole, get_next_message_seq


async def get_conversation_context(
    user_id: str, conversation_id: str, session=None
) -> list[dict]:
    """Stub returns empty context. Wave E1 fills Redis read path."""
    return []


async def append_turn_messages(
    session,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    query: str,
    task_result,
    task_id: uuid.UUID,
    intent: dict | None = None,
    plan: dict | None = None,
) -> None:
    """Stub — Wave D1 fills message persistence. Wave E1 fills Redis push."""
    # User message
    user_seq = await get_next_message_seq(session, conversation_id)
    user_msg = Message(
        conversation_id=conversation_id,
        user_id=user_id,
        role=MessageRole.USER.value,
        content=query,
        seq=user_seq,
    )
    session.add(user_msg)

    # Assistant message
    assistant_seq = await get_next_message_seq(session, conversation_id)
    assistant_msg = Message(
        conversation_id=conversation_id,
        user_id=user_id,
        task_id=task_id,
        role=MessageRole.ASSISTANT.value,
        content=task_result.response,
        seq=assistant_seq,
        intent=intent,
        plan=plan,
        actions_taken=(
            [a.model_dump(mode="json") for a in task_result.actions_taken]
            if task_result.actions_taken
            else None
        ),
        pending_actions=(
            [p.model_dump(mode="json") for p in task_result.pending_actions]
            if task_result.pending_actions
            else None
        ),
    )
    session.add(assistant_msg)

    await session.commit()


async def get_or_create_conversation(
    session,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    title: str | None = None,
):
    """Stub — Wave D2 fills this."""
    raise NotImplementedError("Wave D2 fills this")
