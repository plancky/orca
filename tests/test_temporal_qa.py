"""Unit coverage for ``resolve_timeframe`` — the relative-phrase date resolver.

Pure function, no I/O: a frozen ``now`` makes every window deterministic. The
"last week" / "past week" branch is the one that unblocks retrospective calendar
asks like "what were my meetings in the last week" (which otherwise resolve to no
timeframe and would list *every* event).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.orchestration.utils.temporal import resolve_timeframe

_TZ = ZoneInfo("America/New_York")
# A fixed Wednesday so weekday-relative math is stable across CI runs.
_NOW = datetime(2026, 7, 8, 15, 30, tzinfo=_TZ)


@pytest.mark.parametrize("phrase", ["last week", "past week", "in the last week"])
def test_last_week_is_the_trailing_seven_days(phrase):
    # When: a "last week" phrase is resolved against a fixed now.
    tf = resolve_timeframe(phrase, _NOW, _TZ)

    # Then: the window is the rolling 7 days ending today (00:00 -> 23:59:59).
    assert tf is not None
    start = datetime.fromisoformat(tf["start"])
    end = datetime.fromisoformat(tf["end"])
    assert start.date() == (_NOW.date() - timedelta(days=7))
    assert end.date() == _NOW.date()
    assert start < _NOW < end


def test_next_week_still_points_forward():
    # Given/Then: the pre-existing forward branch is unaffected by the new one.
    tf = resolve_timeframe("next week", _NOW, _TZ)
    assert tf is not None
    assert datetime.fromisoformat(tf["start"]) > _NOW


def test_unknown_phrase_returns_none():
    assert resolve_timeframe("whenever", _NOW, _TZ) is None
