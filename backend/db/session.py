"""Async SQLAlchemy engines.

Two engines by design:

* ``engine`` — pooled, long-lived; used by FastAPI request handlers via the
  ``get_session`` dependency.
* ``celery_engine`` — ``NullPool`` so a Celery prefork child never reuses a
  connection created in a different event loop. ``dispose_celery_engine`` is
  called from the ``worker_process_init`` signal after fork.

The pgvector type codec is registered on BOTH engines through the sync-engine
``connect`` event, so ``vector`` columns round-trip as Python lists.
"""

import json
from typing import Any

from pgvector.asyncpg import register_vector
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from backend.config import settings


def _json_serializer(value: Any) -> str:
    """JSONB serializer tolerant of UUID/datetime (str fallback).

    SQLAlchemy shares this hook between JSON and JSONB on the asyncpg dialect,
    so a stray uuid.UUID/datetime is stringified instead of raising
    'Object of type UUID is not JSON serializable'.
    """
    return json.dumps(value, default=str)

# FastAPI engine (pooled, long-lived)
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    echo=False,
    json_serializer=_json_serializer,
)

# Celery engine (NullPool — no connection reuse across loops)
celery_engine = create_async_engine(
    settings.DATABASE_URL,
    poolclass=NullPool,
    echo=False,
    json_serializer=_json_serializer,
)
async_session_factory = async_sessionmaker(celery_engine, expire_on_commit=False)


# Register pgvector type codec on BOTH engines
@event.listens_for(engine.sync_engine, "connect")
def _register_vector_fastapi(dbapi_conn, conn_rec):
    dbapi_conn.run_async(register_vector)


@event.listens_for(celery_engine.sync_engine, "connect")
def _register_vector_celery(dbapi_conn, conn_rec):
    dbapi_conn.run_async(register_vector)


def dispose_celery_engine() -> None:
    """Reset the Celery engine's pool after prefork.

    Called by the ``worker_process_init`` signal so each child opens fresh
    connections rather than inheriting the parent's (unusable) sockets.
    """
    celery_engine.sync_engine.dispose(close=False)


async def get_session():
    """FastAPI dependency yielding a pooled ``AsyncSession``."""
    async with AsyncSession(engine) as session:
        yield session


__all__ = [
    "engine",
    "celery_engine",
    "async_session_factory",
    "dispose_celery_engine",
    "get_session",
    "text",
]
