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
    """Stub filled by Wave C3.

    Returns the checkpoint from the parent task of an actions_log entry.
    """
    raise NotImplementedError("Wave C3 fills this")
