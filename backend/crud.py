"""Async user CRUD + first-superuser seed.

The hashing calls delegate to ``core.security`` async wrappers, so Argon2 runs
off the event loop. ``authenticate`` uses a single constant-time pattern (verify
against ``DUMMY_HASH`` when the account is missing) to avoid an enumeration
oracle, and auto-persists a pwdlib re-hash when a legacy bcrypt hash is upgraded.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.config import settings
from backend.core.security import DUMMY_HASH, get_password_hash, verify_password
from backend.db.models import User, UserCreate


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def create_user(session: AsyncSession, user_create: UserCreate) -> User:
    hashed_password = await get_password_hash(user_create.password)
    db_user = User.model_validate(
        user_create, update={"hashed_password": hashed_password}
    )
    session.add(db_user)
    await session.commit()
    await session.refresh(db_user)
    return db_user


async def authenticate(
    session: AsyncSession, email: str, password: str
) -> User | None:
    db_user = await get_user_by_email(session, email)
    if db_user is None:
        # Timing guard: pay one verify against a constant hash so a missing
        # account is indistinguishable from a wrong password.
        await verify_password(password, DUMMY_HASH)
        return None
    verified, updated_hash = await verify_password(
        password, db_user.hashed_password
    )
    if not verified:
        return None
    if updated_hash is not None:
        db_user.hashed_password = updated_hash
        session.add(db_user)
        await session.commit()
        await session.refresh(db_user)
    return db_user


async def seed_superuser(session: AsyncSession) -> User | None:
    """Idempotently create the configured first superuser.

    No-op when the superuser already exists or no password is configured.
    """
    password = settings.FIRST_SUPERUSER_PASSWORD
    if not password:
        print("[seed] FIRST_SUPERUSER_PASSWORD unset — skipping superuser seed")
        return None
    email = settings.FIRST_SUPERUSER_EMAIL
    existing = await get_user_by_email(session, email)
    if existing is not None:
        return existing
    user_create = UserCreate(
        email=email,
        password=password,
        full_name="Admin",
        is_superuser=True,
        is_active=True,
    )
    return await create_user(session, user_create)
