from typing import Any

from pydantic import BaseModel


class Intent(BaseModel):
    services: list[str]
    intent: str
    entities: dict[str, Any] = {}
    steps: list[str] = []
    needs_clarification: bool = False
    clarification: str | None = None
