import hashlib
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import redis.asyncio as redis

from backend.config import settings
from backend.context.conversation import get_conversation_context
from backend.llm.client import llm_client
from backend.llm.json_utils import extract_and_validate
from backend.llm.prompts.classifier import CLASSIFIER_PROMPT
from backend.orchestration.models.intent import Intent
from backend.orchestration.utils.temporal import resolve_timeframe

_redis_client = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


async def classify(
    query: str,
    context: list[dict] | None = None,
    now: datetime | None = None,
    tz: ZoneInfo | None = None,
    session: Any = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
) -> Intent:
    if now is None:
        now = datetime.now(tz=tz or ZoneInfo("UTC"))
    if tz is None:
        tz = ZoneInfo("UTC")

    if context is None and user_id is not None and conversation_id is not None:
        context = await get_conversation_context(user_id, conversation_id, session)
    if context is None:
        context = []

    ctx_str = json.dumps(context, sort_keys=True)
    ctx_hash = hashlib.sha256((query + ctx_str).encode()).hexdigest()

    redis_client = _get_redis()
    cache_key = None
    if user_id:
        cache_key = f"user:{user_id}:intent:{ctx_hash}"
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return Intent.model_validate_json(cached)
        except Exception:
            pass  # degrade gracefully if Redis is down

    prompt = CLASSIFIER_PROMPT.format(
        current_datetime=now.isoformat(),
        timezone=str(tz),
        context=json.dumps(context, indent=2),
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": query},
    ]

    raw_response = await llm_client.chat(messages, response_format="json_object")

    intent = await extract_and_validate(
        raw_response, Intent, llm_client=llm_client, schema_name="Intent"
    )

    # Needs clarification short-circuit check
    if intent.needs_clarification:
        return intent

    phrase = intent.entities.get("timeframe_phrase")
    if phrase:
        timeframe = resolve_timeframe(phrase, now, tz)
        if timeframe:
            intent.entities["timeframe"] = timeframe

    if user_id and cache_key:
        try:
            await redis_client.setex(cache_key, 3600, intent.model_dump_json())
        except Exception:
            pass

    return intent
