import json
import logging
import uuid

import redis.asyncio as redis
from sqlmodel import col, select

from backend.config import settings
from backend.db.models import Message, MessageRole, get_next_message_seq

logger = logging.getLogger(__name__)


def _get_redis() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


async def get_conversation_context(
    user_id: str, conversation_id: str, session=None
) -> list[dict]:
    """Reads the rolling last-5 list from Redis, with DB fallback."""
    try:
        r = _get_redis()
        key = f"user:{str(user_id)}:conv:{str(conversation_id)}"
        items = await r.lrange(key, 0, -1)
        if items:
            return [json.loads(item) for item in items]
    except Exception as e:
        logger.warning(f"Failed to read conversation context from Redis: {e}")

    if session is not None:
        try:
            stmt = (
                select(Message)
                .where(Message.conversation_id == str(conversation_id))
                .order_by(col(Message.seq).desc())
                .limit(10)
            )
            result = await session.execute(stmt)
            messages = result.scalars().all()

            messages = list(reversed(messages))

            ctx = []
            current_user_msg = None
            for msg in messages:
                if msg.role == MessageRole.USER.value:
                    current_user_msg = msg
                elif msg.role == MessageRole.ASSISTANT.value and current_user_msg:
                    turn = {
                        "query": current_user_msg.content,
                        "intent": msg.intent.get("intent")
                        if isinstance(msg.intent, dict)
                        else None,
                        "entities": msg.entities
                        if isinstance(msg.entities, dict)
                        else (
                            msg.intent.get("entities", {})
                            if isinstance(msg.intent, dict)
                            else {}
                        ),
                        "result_summary": msg.content,
                    }
                    ctx.append(turn)
                    current_user_msg = None
            return ctx[-5:]
        except Exception as e:
            logger.warning(f"Failed to read conversation context from DB: {e}")

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
        entities=intent.get("entities") if intent else None,
        plan=plan,
        actions_taken=(
            [a.model_dump(mode="json") for a in task_result.actions_taken]
            if getattr(task_result, "actions_taken", None)
            else None
        ),
        pending_actions=(
            [p.model_dump(mode="json") for p in task_result.pending_actions]
            if getattr(task_result, "pending_actions", None)
            else None
        ),
    )
    session.add(assistant_msg)

    await session.commit()

    # Redis push: compact rolling last-5
    try:
        r = _get_redis()
        key = f"user:{str(user_id)}:conv:{str(conversation_id)}"

        intent_name = intent.get("intent") if intent else None
        entities = intent.get("entities", {}) if intent else {}
        turn = {
            "query": query,
            "intent": intent_name,
            "entities": entities,
            "result_summary": task_result.response,
        }

        async with r.pipeline(transaction=True) as pipe:
            pipe.rpush(key, json.dumps(turn))
            pipe.ltrim(key, -5, -1)
            pipe.expire(key, 3600)
            await pipe.execute()
    except Exception as e:
        logger.warning(f"Failed to push turn to Redis: {e}")
