"""Plain-assert tests for the pure scheduling maths.

Runnable with a bare interpreter (`python tests/test_schedule.py`) and also
pytest-compatible. No fixtures/mocks: schedule.* takes now/tz as arguments, so we
just pin a fixed epoch anchor and a timezone.
"""

from __future__ import annotations

import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naggy import schedule
from naggy.models import Reminder

UTC = ZoneInfo("UTC")
DAY = 86_400
WEEK = 604_800

# 2026-01-31 12:00:00 UTC — chosen so month arithmetic must clamp the day.
JAN31 = 1_769_860_800


def test_day_interval():
    assert schedule.next_due(0, 1, "day", UTC) == DAY, schedule.next_due(0, 1, "day", UTC)
    assert schedule.next_due(0, 3, "day", UTC) == 3 * DAY


def test_week_interval():
    assert schedule.next_due(0, 2, "week", UTC) == 2 * WEEK, schedule.next_due(0, 2, "week", UTC)


def test_month_clamps_to_end_of_february():
    # Jan 31 + 1 month must land on Feb 28 (2026 is not a leap year), same time.
    got = schedule.next_due(JAN31, 1, "month", UTC)
    from datetime import datetime
    d = datetime.fromtimestamp(got, UTC)
    assert (d.year, d.month, d.day) == (2026, 2, 28), (d.year, d.month, d.day)
    assert (d.hour, d.minute) == (12, 0), (d.hour, d.minute)


def test_month_rolls_over_year():
    # Jan 31 + 12 months = next Jan 31.
    got = schedule.next_due(JAN31, 12, "month", UTC)
    from datetime import datetime
    d = datetime.fromtimestamp(got, UTC)
    assert (d.year, d.month, d.day) == (2027, 1, 31), (d.year, d.month, d.day)


def test_year_interval():
    got = schedule.next_due(JAN31, 1, "year", UTC)
    from datetime import datetime
    d = datetime.fromtimestamp(got, UTC)
    assert (d.year, d.month, d.day) == (2027, 1, 31), (d.year, d.month, d.day)


def test_bad_inputs_raise():
    for bad in (0, -1):
        try:
            schedule.next_due(0, bad, "day", UTC)
            assert False, "expected ValueError"
        except ValueError:
            pass
    try:
        schedule.next_due(0, 1, "fortnight", UTC)
        assert False, "expected ValueError"
    except ValueError:
        pass


def _r(due_at, active=True):
    return Reminder(title="x", due_at=due_at, active=active)


def test_board_splits_and_sorts():
    now = 1000
    rems = [
        _r(now + 50),        # upcoming (sooner)
        _r(now - 10),        # pending (less overdue)
        _r(now - 100),       # pending (more overdue)
        _r(now + 200),       # upcoming (later)
        _r(now - 5, active=False),  # archived -> excluded
    ]
    b = schedule.board(rems, now)
    assert [r.due_at for r in b["pending"]] == [now - 100, now - 10], b["pending"]
    assert [r.due_at for r in b["upcoming"]] == [now + 50, now + 200], b["upcoming"]


def test_board_pending_includes_exactly_now():
    now = 1000
    b = schedule.board([_r(now)], now)
    assert len(b["pending"]) == 1 and not b["upcoming"], b


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
