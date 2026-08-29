"""Session and economic-event blackout filtering.

Sessions are expressed in UTC HH:MM. Because we compare against UTC epoch
timestamps, daylight-saving changes in any local timezone do not affect the
session filters (this is intentional and testable).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.strategy import SessionWindow


def _m_to_sec(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60


def in_session(ts: float, sessions: list[SessionWindow]) -> bool:
    """Return True if the given UTC epoch is inside any configured session.

    Supports sessions that wrap past midnight (start > end).
    """
    day_seconds = ts % 86400
    for window in sessions:
        start = _m_to_sec(window.start)
        end = _m_to_sec(window.end)
        if start <= end:
            if start <= day_seconds < end:
                return True
        else:  # wraps past midnight
            if day_seconds >= start or day_seconds < end:
                return True
    return False


def is_blackout(
    ts: float,
    events: list[dict],
    before_min: int = 15,
    after_min: int = 15,
) -> bool:
    """Return True if ts is within before/after minutes of a high-impact event."""
    if not events:
        return False
    for ev in events:
        if ev.get("impact", "").lower() != "high":
            continue
        evt = ev["event_time"]
        if evt - before_min * 60 <= ts <= evt + after_min * 60:
            return True
    return False
