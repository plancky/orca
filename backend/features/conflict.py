from backend.orchestration.utils.tools import register_tool


@register_tool("conflict.detect")
async def detect_overlaps(
    events: list[dict], time_window: dict | None = None
) -> list[dict]:
    """Stub returns empty list. Wave E2 fills the body."""
    return []
