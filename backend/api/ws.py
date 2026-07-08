import asyncio
import json
import logging
import uuid

import jwt
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_redis
from backend.config import settings
from backend.core.security import ALGORITHM
from backend.db.models import Conversation, Task, TaskStatus, TokenPayload, User
from backend.db.session import get_session
from backend.workers.orchestrate import run_pipeline

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


async def get_ws_user(token: str | None, session: AsyncSession) -> User | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        token_data = TokenPayload(**payload)
        if token_data.sub is None:
            return None
        user_id = uuid.UUID(token_data.sub)
    except (InvalidTokenError, ValidationError, ValueError):
        return None
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


@router.websocket("/ws/query")
async def ws_query(
    websocket: WebSocket,
    token: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
):
    await websocket.accept()

    user = await get_ws_user(token, session)
    if not user:
        await websocket.close(code=1008, reason="Invalid or missing token")
        return

    try:
        data = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
        await websocket.close(code=1003, reason="Expected JSON message")
        return

    task_id_str = data.get("task_id")
    query = data.get("query")
    conversation_id_str = data.get("conversation_id")

    if query:
        # We need to create a conversation if one wasn't provided?
        # Or wait, does the endpoint accept missing conversation_id?
        # "enqueue run_pipeline.delay(...) then attach; send first frame"
        if conversation_id_str:
            conv_id = uuid.UUID(conversation_id_str)
        else:
            conv = Conversation(user_id=user.id, title=query[:50])
            session.add(conv)
            await session.commit()
            conv_id = conv.id

        new_task = Task(
            user_id=user.id, conversation_id=conv_id, status=TaskStatus.QUEUED.value
        )
        session.add(new_task)
        await session.commit()
        task_id_str = str(new_task.id)

        # Enqueue Celery task
        run_pipeline.delay(
            task_id=task_id_str,
            user_id=str(user.id),
            query=query,
            conversation_id=str(conv_id),
        )

        try:
            await websocket.send_json({"type": "task_created", "task_id": task_id_str})
        except WebSocketDisconnect:
            return

    if not task_id_str:
        await websocket.close(code=1003, reason="Missing task_id or query")
        return

    task_id = str(task_id_str)
    stream_key = f"stream:tasks:{task_id}"
    last_id = "0-0"

    # Check if task is already terminal before streaming?
    # stream until we see a done event, or check if terminal in DB.

    task_row = await session.get(Task, uuid.UUID(task_id))
    if not task_row:
        await websocket.close(code=1003, reason="Task not found")
        return

    # Keep a reference to whether we've sent 'done' to avoid duplicate
    sent_done = False

    try:
        while True:
            # Check DB if terminal and we're caught up?
            # It's better to just xread and if we get a terminal event, we end.
            # But what if the task finished and the stream is empty or we missed it?

            # Read from stream
            # block for 2 seconds
            messages = await redis.xread({stream_key: last_id}, count=10, block=2000)

            if messages:
                for _stream, msgs in messages:
                    for msg_id, msg_data in msgs:
                        last_id = msg_id

                        if "payload" in msg_data:
                            try:
                                event = json.loads(msg_data["payload"])
                                await websocket.send_json(event)
                                if event.get("type") == "done":
                                    sent_done = True
                                    break
                            except Exception as e:
                                logger.error(f"Failed to parse or send event: {e}")

            if sent_done:
                break

            # If no messages in stream, or we didn't see a 'done' event, let's check DB.
            # Maybe the stream expired or the task finished without publishing 'done'?
            # Actually, `executor.py` publishes 'done' event. But let's be safe.
            await session.refresh(task_row)
            terminals = (
                TaskStatus.SUCCESS.value,
                TaskStatus.FAILED.value,
                TaskStatus.AWAITING_CONFIRMATION.value,
            )
            if task_row.status in terminals:
                # The task is terminal. If we lack a done event, emit one.
                # Actually, wait, let's just emit one and break.
                if not sent_done:
                    await websocket.send_json(
                        {
                            "type": "done",
                            "task_id": task_id,
                            "timestamp": task_row.updated_at.isoformat(),
                            "payload": task_row.result or {"status": task_row.status},
                        }
                    )
                break

            # Yield to event loop and check for disconnect
            # websocket receive is pending, cant easily wait on it and xread.
            # We'll just rely on xread timeout to check for disconnect?
            # FastAPI websockets dont throw Disconnect on send if dropped silently,
            # but usually send_json will raise WebSocketDisconnect.

    except WebSocketDisconnect:
        logger.info(f"Client disconnected from task {task_id}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
