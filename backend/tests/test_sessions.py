from datetime import datetime, timezone

from app.backtest.sessions import in_session, is_blackout
from app.schemas.strategy import SessionWindow

LONDON = SessionWindow(name="London", start="07:00", end="12:00")
ASIAN = SessionWindow(name="Asian", start="00:00", end="07:00")
OVERNIGHT = SessionWindow(name="Overnight", start="20:00", end="04:00")


def _uts(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp()


def test_session_inside():
    # 2024-01-15 (winter) 09:30 UTC -> inside London
    assert in_session(_uts(2024, 1, 15, 9, 30), [LONDON])


def test_session_outside():
    assert not in_session(_uts(2024, 1, 15, 13, 0), [LONDON])
    assert not in_session(_uts(2024, 1, 15, 5, 0), [LONDON])


def test_session_end_boundary_exclusive():
    assert not in_session(_uts(2024, 1, 15, 12, 0), [LONDON])


def test_session_dst_change_uses_utc_epoch():
    # Summer and winter dates at the same UTC time must behave identically
    # because sessions are compared against UTC epoch seconds only.
    assert in_session(_uts(2024, 6, 15, 9, 0), [LONDON])  # summer
    assert in_session(_uts(2024, 1, 15, 9, 0), [LONDON])  # winter
    assert not in_session(_uts(2024, 7, 1, 13, 0), [LONDON])
    assert not in_session(_uts(2024, 12, 1, 13, 0), [LONDON])


def test_overnight_wrap_around():
    assert in_session(_uts(2024, 1, 15, 2, 0), [OVERNIGHT])   # after midnight
    assert in_session(_uts(2024, 1, 15, 21, 0), [OVERNIGHT])  # before midnight
    assert not in_session(_uts(2024, 1, 15, 12, 0), [OVERNIGHT])


def test_no_blackout_when_no_events():
    assert not is_blackout(_uts(2024, 1, 15, 9, 0), [])


def test_blackout_within_window():
    ev = [{"impact": "high", "event_time": _uts(2024, 1, 15, 9, 0)}]
    # 10 min before
    assert is_blackout(_uts(2024, 1, 15, 8, 50), ev, 15, 15)
    # 10 min after
    assert is_blackout(_uts(2024, 1, 15, 9, 10), ev, 15, 15)


def test_not_blackout_outside_window():
    ev = [{"impact": "high", "event_time": _uts(2024, 1, 15, 9, 0)}]
    assert not is_blackout(_uts(2024, 1, 15, 10, 0), ev, 15, 15)


def test_low_impact_ignored():
    ev = [{"impact": "low", "event_time": _uts(2024, 1, 15, 9, 0)}]
    assert not is_blackout(_uts(2024, 1, 15, 9, 0), ev, 15, 15)
