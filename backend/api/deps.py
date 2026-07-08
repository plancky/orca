"""FastAPI auth dependencies.

``get_current_user`` decodes the bearer token, hydrates a ``TokenPayload``, and
loads the ``User`` — funnelling every decode/validation failure through a single
401 (with ``WWW-Authenticate: Bearer``). The ``except`` is a *parenthesized
tuple* on purpose: the Python-2 ``except A, B:`` form the full-stack template
historically shipped is a bug and is deliberately not reproduced here.
"""

from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import jwt
import redis.asyncio as aioredis
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.security import ALGORITHM
from backend.db.models import TokenPayload, User
from backend.db.session import get_session

reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token"
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
TokenDep = Annotated[str, Depends(reusable_oauth2)]


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(session: SessionDep, token: TokenDep) -> User:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        token_data = TokenPayload(**payload)
    except (InvalidTokenError, ValidationError) as exc:
        raise _credentials_exception() from exc
    if token_data.sub is None:
        raise _credentials_exception()
    try:
        user_id = UUID(token_data.sub)
    except ValueError as exc:
        raise _credentials_exception() from exc
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_active_superuser(current_user: CurrentUser) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=403, detail="The user doesn't have enough privileges"
        )
    return current_user


# --------------------------------------------------------------------------- #
# Rate limiting — Redis fixed-window token bucket, 100 requests/user/hour.
# Provided here for the /query routes (Wave D) to attach; not gating auth.
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def get_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


async def enforce_rate_limit(current_user: CurrentUser) -> None:
    window = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    key = f"ratelimit:{current_user.id}:{window}"
    redis = get_redis()
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 3600)
    if count > settings.RATE_LIMIT_PER_USER_PER_HOUR:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
