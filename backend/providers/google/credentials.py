"""Google OAuth credential storage + refresh (Phase 2).

Tokens are Fernet-encrypted at rest in ``users.google_access_token`` /
``google_refresh_token``. ``credentials_for`` rebuilds a
``google.oauth2.credentials.Credentials`` and refreshes it under a per-user
Redis lock, because refresh-token rotation is racy across the API path and the
sync worker. A revoked/expired refresh token (``invalid_grant``) flips
``users.auth_status`` to ``invalid`` so the sync beat skips the user until
re-consent.

Every ``google*`` / ``cryptography`` import is lazy so the mock/offline path and
the CPU-only image never load them.
"""

import asyncio
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis

from backend.config import settings
from backend.db.models import User

_REFRESH_LOCK_TTL = 30


def _fernet():
    from cryptography.fernet import Fernet

    key = settings.TOKEN_ENCRYPTION_KEY
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is required when PROVIDER=google")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


def default_scopes() -> list[str]:
    return settings.GOOGLE_SCOPES.split()


def _to_aware(expiry: datetime | None) -> datetime | None:
    if expiry is None:
        return None
    return expiry.replace(tzinfo=timezone.utc) if expiry.tzinfo is None else expiry


def _to_naive_utc(expiry: datetime | None) -> datetime | None:
    if expiry is None:
        return None
    return expiry.astimezone(timezone.utc).replace(tzinfo=None)


def build_credentials(user: User):
    """Rebuild google Credentials from the user's decrypted tokens (no refresh)."""
    from google.oauth2.credentials import Credentials

    access = (
        decrypt_token(user.google_access_token)
        if user.google_access_token
        else None
    )
    refresh = (
        decrypt_token(user.google_refresh_token)
        if user.google_refresh_token
        else None
    )
    return Credentials(
        token=access,
        refresh_token=refresh,
        token_uri=settings.GOOGLE_TOKEN_URI,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=user.token_scopes or default_scopes(),
        expiry=_to_naive_utc(user.token_expiry),
    )


async def store_credentials(session, user: User, creds) -> None:
    """Encrypt + persist creds onto the user row.

    Google omits the refresh token on rotation-less refreshes, so the existing
    one is preserved when ``creds.refresh_token`` is empty.
    """
    if creds.token:
        user.google_access_token = encrypt_token(creds.token)
    if creds.refresh_token:
        user.google_refresh_token = encrypt_token(creds.refresh_token)
    user.token_expiry = _to_aware(creds.expiry)
    user.token_scopes = list(creds.scopes) if creds.scopes else default_scopes()
    user.auth_status = "valid"
    session.add(user)
    await session.commit()
    await session.refresh(user)


async def mark_invalid(session, user: User) -> None:
    user.auth_status = "invalid"
    session.add(user)
    await session.commit()


async def credentials_for(session, user_id: str | uuid.UUID):
    """Load valid Credentials for a user id, refreshing once if expired."""
    user = await session.get(User, uuid.UUID(str(user_id)))
    if user is None:
        raise RuntimeError(f"user {user_id} not found")
    if user.auth_status == "invalid":
        raise RuntimeError("Google auth invalid for user — re-consent required")
    creds = build_credentials(user)
    if not creds.expired or not creds.refresh_token:
        return creds
    return await _refresh_under_lock(session, user)


async def _refresh_under_lock(session, user: User):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request

    r = aioredis.from_url(settings.REDIS_URL)
    lock_key = f"lock:google_refresh:{user.id}"
    try:
        acquired = await r.set(lock_key, "1", nx=True, ex=_REFRESH_LOCK_TTL)
        if not acquired:
            # Another worker is rotating the token; wait then reload its result.
            await asyncio.sleep(1.0)
            await session.refresh(user)
            return build_credentials(user)
        creds = build_credentials(user)
        try:
            await asyncio.to_thread(creds.refresh, Request())
        except RefreshError as exc:
            if "invalid_grant" in str(exc):
                await mark_invalid(session, user)
            raise
        await store_credentials(session, user, creds)
        return creds
    finally:
        await r.delete(lock_key)
        await r.aclose()
