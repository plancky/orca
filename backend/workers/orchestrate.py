import asyncio

from backend.workers.celery_app import app


# Module-level importable coroutine — eval/evaluate.py calls this directly,
# bypassing Celery.
async def pipeline(
    task_id: str,
    user_id: str,
    query: str,
    conversation_id: str | None = None,
    confirm=None,
) -> dict:
    """Full classify->plan->execute->synth pipeline. Wave D1 fills this body."""
    raise NotImplementedError("Wave D1 fills this")


@app.task(name="backend.workers.orchestrate.run_pipeline", bind=True)
def run_pipeline(
    self,
    task_id: str,
    user_id: str,
    query: str,
    conversation_id: str | None = None,
    confirm=None,
):
    """Sync Celery shell — wraps async pipeline."""
    return asyncio.run(pipeline(task_id, user_id, query, conversation_id, confirm))
