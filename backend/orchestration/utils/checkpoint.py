import uuid
from typing import Any

from pydantic import BaseModel

from backend.orchestration.models.dag import Plan
from backend.orchestration.models.intent import Intent


class Checkpoint(BaseModel):
    intent: Intent
    plan: Plan
    node_outputs: dict[str, Any] = {}
    pending_node_id: str
    remaining_node_ids: list[str] = []
    context: dict[str, Any] = {}
    resumed_from: uuid.UUID | None = None

    def dump(self) -> str:
        return self.model_dump_json()

    @classmethod
    def load(cls, data: str) -> "Checkpoint":
        return cls.model_validate_json(data)


async def get_checkpoint_for_action(
    session, action_id: uuid.UUID
) -> "Checkpoint | None":
    """Return the checkpoint from the parent task of an actions_log entry."""
    from backend.db.models import ActionsLog, Task

    log_row = await session.get(ActionsLog, action_id)
    if not log_row or not log_row.task_id:
        return None

    task_row = await session.get(Task, log_row.task_id)
    if not task_row or not task_row.checkpoint:
        return None

    # Handle both string (JSON) and dict (JSONB parsed by SQLAlchemy)
    if isinstance(task_row.checkpoint, str):
        return Checkpoint.load(task_row.checkpoint)
    return Checkpoint.model_validate(task_row.checkpoint)
