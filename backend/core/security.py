"""JWT tokens + password hashing (Argon2-primary, bcrypt fallback).

Argon2 is CPU-bound *by design*; running it on the event loop would stall every
concurrent request. The public ``get_password_hash`` / ``verify_password`` are
therefore async wrappers that push the work onto a worker thread via
``anyio.to_thread.run_sync`` — callers ``await`` them and the loop stays free.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import anyio
import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

from backend.config import settings

ALGORITHM = "HS256"

# Argon2 is the primary hasher; bcrypt stays registered so legacy bcrypt hashes
# still verify (and get transparently upgraded to Argon2 on next login).
password_hash = PasswordHash((Argon2Hasher(), BcryptHasher()))

# A constant, valid Argon2 hash. ``authenticate`` verifies a would-be password
# against this when no user row exists, so "unknown email" and "wrong password"
# both pay exactly one Argon2 verify — no timing oracle for account enumeration.
DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$q4e394xhoDvhZ2XDAfEBFA$"
    "jfwlns2JPJGDkeEv+Fiwt0J8nJcPal4XNta3CzDdgYU"
)


def create_access_token(subject: str | UUID, expires_delta: timedelta) -> str:
    """Sign an HS256 access token carrying ``sub`` (subject) and ``exp``."""
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {"sub": str(subject), "exp": expire}
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


async def get_password_hash(password: str) -> str:
    """Hash ``password`` (Argon2) off the event loop."""
    return await anyio.to_thread.run_sync(password_hash.hash, password)


async def verify_password(plain: str, hashed: str) -> tuple[bool, str | None]:
    """Verify ``plain`` against ``hashed`` off the event loop.

    Returns ``(verified, updated_hash)``; ``updated_hash`` is non-``None`` only
    when pwdlib re-hashes an outdated (e.g. bcrypt) hash to Argon2.
    """
    return await anyio.to_thread.run_sync(
        password_hash.verify_and_update, plain, hashed
    )
