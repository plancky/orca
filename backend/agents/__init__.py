"""Agent tool surface.

Importing ``backend.agents`` is the single side-effecting step that populates the
Wave-0 ``REGISTRY``: importing the three agent modules runs their
``@register_tool`` decorators, and importing ``backend.features.conflict`` (the
Wave-0 stub that self-registers ``conflict.detect``) rounds out the set. So
``import backend.agents`` alone guarantees all 15 agent tools **plus**
``conflict.detect`` are registered.

``WRITE_TOOLS`` is the gated set the executor (Wave C) reads to suspend on a
write BEFORE calling the tool (PLAN.md l.506-508).
"""

import backend.features.conflict  # noqa: F401  -- self-registers conflict.detect
from backend.agents import drive, gcal, gmail  # noqa: F401  -- register tools

WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "gmail.send_email",
        "gmail.update_labels",
        "gcal.create_event",
        "gcal.update_event",
        "gcal.delete_event",
        "drive.share_file",
        "drive.move_file",
    }
)

__all__ = ["WRITE_TOOLS", "drive", "gcal", "gmail"]
