import re
from typing import Any

from pydantic import BaseModel

DEFERRED_REF_RE = re.compile(r'^\$n(\d+)\.(\w+(?:\.\w+)*)$')


class DeferredArgResolutionError(Exception):
    pass


class Node(BaseModel):
    id: str
    tool: str
    args: dict[str, Any] = {}
    depends_on: list[str] = []
    optional: bool = False
    on_missing: str | None = None


class Plan(BaseModel):
    nodes: list[Node]
