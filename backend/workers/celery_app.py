import asyncio

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from backend.config import settings

app = Celery(
    "workspace_orchestrator",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "backend.workers.orchestrate",
        "backend.workers.confirm",
        "backend.workers.sync",
    ],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "sync-15min": {
            "task": "backend.workers.sync.sync_all_users",
            "schedule": settings.SYNC_BEAT_MINUTES * 60,
        }
    },
)


# One asyncio loop per prefork child, reused across every task it runs. Do NOT
# revert to asyncio.run(): it closes its loop per call, but the module-level
# embedder/llm_client singletons cache async clients bound to the loop that
# first created them — the next task then reuses that closed loop and raises
# "Event loop is closed". A single long-lived loop keeps those clients valid.
_loop: asyncio.AbstractEventLoop | None = None


def _run(coro):
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop.run_until_complete(coro)


@worker_process_init.connect
def init_worker(**kwargs):
    """Reset NullPool after prefork so each child opens fresh connections."""
    from backend.db.session import dispose_celery_engine

    dispose_celery_engine()


@worker_process_shutdown.connect
def shutdown_worker(**kwargs):
    # Close cached clients on the loop that owns their sockets, then the loop.
    global _loop
    if _loop is None or _loop.is_closed():
        return
    from backend.embeddings.embedder import embedder
    from backend.llm.client import llm_client

    async def _aclose():
        await embedder.aclose()
        await llm_client.aclose()

    try:
        _loop.run_until_complete(_aclose())
        _loop.run_until_complete(_loop.shutdown_asyncgens())
    finally:
        _loop.close()
        _loop = None


# Populate `app.tasks` at import time. The `include=[...]` list above is only
# imported on worker bootstrap, but the eval harness + offline checks import
# this app module directly. `import_default_modules()` imports exactly that
# `include` list (single source of truth), binding the three @app.task shells.
app.loader.import_default_modules()
