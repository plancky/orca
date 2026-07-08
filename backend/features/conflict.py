from datetime import datetime

from backend.orchestration.utils.tools import register_tool


def _parse_time(t) -> datetime | None:
    if not t:
        return None
    if isinstance(t, datetime):
        return t
    try:
        t_str = str(t).replace("Z", "+00:00")
        return datetime.fromisoformat(t_str)
    except Exception:
        return None

def find_overlaps(events: list[dict], time_window: dict | None = None) -> list[dict]:
    """
    Finds overlapping events.
    If time_window is provided, returns events that overlap with that window.
    If no time_window is provided, returns all events that overlap with at least
    one other event.
    Returns [] on malformed input or if no overlaps found.
    """
    if not isinstance(events, list):
        return []

    parsed_events = []
    for e in events:
        if not isinstance(e, dict):
            continue
        start = _parse_time(e.get("start_at"))
        end = _parse_time(e.get("end_at"))
        if start and end and start < end:
            parsed_events.append((start, end, e))
            
    if time_window is not None:
        if not isinstance(time_window, dict):
            return []
        w_start = _parse_time(time_window.get("start"))
        w_end = _parse_time(time_window.get("end"))
        if not w_start or not w_end or w_start >= w_end:
            return []
            
        overlaps = []
        for s, e, orig in parsed_events:
            if s < w_end and w_start < e:
                overlaps.append(orig)
        return overlaps
        
    else:
        overlaps = []
        n = len(parsed_events)
        overlap_indices = set()
        for i in range(n):
            for j in range(i + 1, n):
                s1, e1, _ = parsed_events[i]
                s2, e2, _ = parsed_events[j]
                if s1 < e2 and s2 < e1:
                    overlap_indices.add(i)
                    overlap_indices.add(j)
                    
        for idx in sorted(overlap_indices):
            overlaps.append(parsed_events[idx][2])
            
        return overlaps

@register_tool("conflict.detect")
async def detect_overlaps(session, user_id, args: dict) -> list[dict]:
    """Registered tool wrapper matching standard signature."""
    events = args.get("events", [])
    time_window = args.get("time_window")
    return find_overlaps(events, time_window)
