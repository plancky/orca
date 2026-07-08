
from backend.workers.celery_app import app


async def sync_all_async(user_id: str) -> dict:
    """Per-user sync: fetch -> upsert datasource -> chunk+embed -> write vector_store.

    Wave D3 fills.
    """
    raise NotImplementedError("Wave D3 fills this")


@app.task(name="backend.workers.sync.sync_all_users")
def sync_all_users():
    """Beat task: sync all active users. Wave D3 fills."""
    raise NotImplementedError("Wave D3 fills this")
