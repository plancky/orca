import uuid
from typing import Any

from pydantic import BaseModel


class ActionSummary(BaseModel):
    tool: str
    args: dict[str, Any] = {}
    result: Any = None
    status: str = "executed"


class PendingAction(BaseModel):
    action_id: uuid.UUID
    tool: str
    args: dict[str, Any] = {}
    preview: str = ""


class TaskResult(BaseModel):
    response: str
    actions_taken: list[ActionSummary] = []
    pending_actions: list[PendingAction] | None = None
