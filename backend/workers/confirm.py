import asyncio

from backend.workers.celery_app import app


async def resume(
    checkpoint_json: str, decision: str, task_id: str, user_id: str
) -> dict:
    """Resume-from-checkpoint pipeline. Wave D1 fills this body."""
    raise NotImplementedError("Wave D1 fills this")


@app.task(name="backend.workers.confirm.run_resume", bind=True)
def run_resume(self, checkpoint_json: str, decision: str, task_id: str, user_id: str):
    return asyncio.run(resume(checkpoint_json, decision, task_id, user_id))
