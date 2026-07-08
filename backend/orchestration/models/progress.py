from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class NodeStartedEvent(BaseModel):
    type: Literal["node_started"]
    task_id: str
    node_id: str
    timestamp: datetime
    payload: dict[str, Any] = {}


class NodeFinishedEvent(BaseModel):
    type: Literal["node_finished"]
    task_id: str
    node_id: str
    timestamp: datetime
    payload: dict[str, Any] = {}


class PartialEvent(BaseModel):
    type: Literal["partial"]
    task_id: str
    node_id: str | None = None
    timestamp: datetime
    payload: dict[str, Any] = {}


class SuspendedEvent(BaseModel):
    type: Literal["suspended"]
    task_id: str
    node_id: str
    timestamp: datetime
    payload: dict[str, Any] = {}


class DoneEvent(BaseModel):
    type: Literal["done"]
    task_id: str
    timestamp: datetime
    payload: dict[str, Any] = {}


ProgressEvent = (
    NodeStartedEvent | NodeFinishedEvent | PartialEvent | SuspendedEvent | DoneEvent
)
