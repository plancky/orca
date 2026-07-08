import uuid


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
) -> None:
    """Stub — Wave D1 fills message persistence. Wave E1 fills Redis push."""
    pass


async def get_or_create_conversation(
    session,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    title: str | None = None,
):
    """Stub — Wave D2 fills this."""
    raise NotImplementedError("Wave D2 fills this")
