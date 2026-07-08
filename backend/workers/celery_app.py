from celery import Celery
from celery.signals import worker_process_init

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


@worker_process_init.connect
def init_worker(**kwargs):
    """Reset NullPool after prefork so each child opens fresh connections."""
    from backend.db.session import dispose_celery_engine

    dispose_celery_engine()


# Populate `app.tasks` at import time. The `include=[...]` list above is only
# imported on worker bootstrap, but the eval harness + offline checks import
# this app module directly. `import_default_modules()` imports exactly that
# `include` list (single source of truth), binding the three @app.task shells.
app.loader.import_default_modules()
