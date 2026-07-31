"""Plain-assert tests for the pure scheduling maths.

Runnable with a bare interpreter (`python tests/test_schedule.py`) and also
pytest-compatible. No fixtures/mocks: schedule.* takes now/tz as arguments, so we
just pin a fixed epoch anchor and a timezone.

Naggy is date-grained, so most of what's checked here is that `next_due` lands on
a *local midnight* — the assertions read the result back through the timezone
rather than comparing raw epoch arithmetic, because a fixed multiple of 86_400 is
exactly the wrong answer around a DST change.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naggy import schedule
from naggy.constants import humanize_days
from naggy.models import Reminder

UTC = ZoneInfo("UTC")
MELB = ZoneInfo("Australia/Melbourne")
DAY = 86_400
WEEK = 604_800

# 2026-01-31 12:00:00 UTC — chosen so month arithmetic must clamp the day.
JAN31 = 1_769_860_800


def _epoch(y, mo, d, h=0, mi=0, tz=MELB):
    return int(datetime(y, mo, d, h, mi, tzinfo=tz).timestamp())


def _parts(epoch, tz=MELB):
    d = datetime.fromtimestamp(epoch, tz)
    return (d.year, d.month, d.day, d.hour, d.minute)


# --- next_due lands on local midnight ----------------------------------------


def test_day_interval_lands_on_midnight():
    # Added mid-afternoon, repeating every 2 days -> due at 00:00 two days later,
    # not at 15:20 two days later. This is the bug that started all this.
    got = schedule.next_due(_epoch(2026, 7, 26, 15, 20), 2, "day", MELB)
    assert _parts(got) == (2026, 7, 28, 0, 0), _parts(got)


def test_time_of_day_is_discarded():
    # Two completions on the same local date must produce the same due date, even
    # though they're most of a day apart.
    early = schedule.next_due(_epoch(2026, 7, 26, 0, 10), 3, "day", MELB)
    late = schedule.next_due(_epoch(2026, 7, 26, 23, 50), 3, "day", MELB)
    assert early == late == _epoch(2026, 7, 29), (_parts(early), _parts(late))


def test_week_interval():
    got = schedule.next_due(_epoch(2026, 7, 26, 9, 0), 2, "week", MELB)
    assert _parts(got) == (2026, 8, 9, 0, 0), _parts(got)


def test_utc_day_interval_is_a_clean_multiple():
    # In UTC, where no DST shifts the day boundary, the maths still reduces to the
    # obvious thing.
    assert schedule.next_due(0, 1, "day", UTC) == DAY
    assert schedule.next_due(0, 2, "week", UTC) == 2 * WEEK


def test_day_interval_survives_a_dst_change():
    # Melbourne leaves DST at 03:00 on 5 Apr 2026 (clocks go back an hour). Adding
    # 7 * 86_400 seconds across that would land at 23:00 the previous day; the
    # date arithmetic must still give midnight on the 8th.
    got = schedule.next_due(_epoch(2026, 4, 1, 10, 0), 1, "week", MELB)
    assert _parts(got) == (2026, 4, 8, 0, 0), _parts(got)


def test_month_clamps_to_end_of_february():
    # Jan 31 + 1 month must land on Feb 28 (2026 is not a leap year), at midnight.
    got = schedule.next_due(JAN31, 1, "month", UTC)
    assert _parts(got, UTC) == (2026, 2, 28, 0, 0), _parts(got, UTC)


def test_month_rolls_over_year():
    got = schedule.next_due(JAN31, 12, "month", UTC)
    assert _parts(got, UTC) == (2027, 1, 31, 0, 0), _parts(got, UTC)


def test_year_interval():
    got = schedule.next_due(JAN31, 1, "year", UTC)
    assert _parts(got, UTC) == (2027, 1, 31, 0, 0), _parts(got, UTC)


def test_month_hop_uses_the_local_date_not_utc():
    # 22:30 on 31 Jul in Melbourne is still midday on the 31st in UTC, but the hop
    # has to be computed on the *local* date either way.
    got = schedule.next_due(_epoch(2026, 7, 31, 22, 30), 1, "month", MELB)
    assert _parts(got) == (2026, 8, 31, 0, 0), _parts(got)


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


# --- start_of_day / days_until -----------------------------------------------


def test_start_of_day_rounds_down():
    assert schedule.start_of_day(_epoch(2026, 7, 28, 23, 59), MELB) == _epoch(2026, 7, 28)
    # Already midnight: unchanged, not pushed back a whole day.
    assert schedule.start_of_day(_epoch(2026, 7, 28), MELB) == _epoch(2026, 7, 28)


def test_due_from_date_lands_on_local_midnight():
    assert schedule.due_from_date("2026-08-14", MELB) == _epoch(2026, 8, 14)
    assert _parts(schedule.due_from_date("2026-08-14", MELB)) == (2026, 8, 14, 0, 0)


def test_due_from_date_is_relative_to_the_configured_zone():
    # The same picked date is a different instant in each zone — the point of
    # passing tz in rather than reading a global.
    assert schedule.due_from_date("2026-08-14", MELB) != schedule.due_from_date("2026-08-14", UTC)


def test_due_from_date_survives_a_dst_change():
    # Melbourne enters DST at 02:00 on 4 Oct 2026; midnight on the 4th is still
    # midnight, and the day before it is 24 hours earlier, not 23.
    assert _parts(schedule.due_from_date("2026-10-04", MELB)) == (2026, 10, 4, 0, 0)


def test_due_from_date_rejects_junk():
    for bad in ("", "14/08/2026", "2026-13-01", "tomorrow"):
        try:
            schedule.due_from_date(bad, MELB)
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_a_picked_start_date_turns_pending_at_its_midnight():
    """A chore started on a chosen day behaves exactly like a cadence-derived one:
    quiet until that date, pending from its first second."""
    r = _r(schedule.due_from_date("2026-08-14", MELB))
    assert not r.is_pending(_epoch(2026, 8, 13, 23, 59))
    assert r.is_pending(_epoch(2026, 8, 14, 0, 0))


def test_days_until_counts_dates_not_elapsed_time():
    # 23:00 tonight to 00:00 tomorrow is one hour, but it is still a day away.
    now = _epoch(2026, 7, 28, 23, 0)
    assert schedule.days_until(_epoch(2026, 7, 29), now, MELB) == 1
    assert schedule.days_until(_epoch(2026, 7, 28), now, MELB) == 0
    assert schedule.days_until(_epoch(2026, 7, 26), now, MELB) == -2


def test_days_until_is_timezone_sensitive():
    # 15:00 UTC on the 28th is already 01:00 on the 29th in Melbourne, so the same
    # pair of instants is "tomorrow" in one zone and "today" in the other.
    now = _epoch(2026, 7, 28, 15, 0, tz=UTC)
    due = _epoch(2026, 7, 29, 3, 0, tz=UTC)
    assert schedule.days_until(due, now, UTC) == 1
    assert schedule.days_until(due, now, MELB) == 0


# --- the board ----------------------------------------------------------------


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


def test_reminder_turns_pending_at_midnight():
    """The reported bug, end to end: a chore added two days ago on an every-2-days
    cadence is pending from 00:00 on the due date, not from the time of day it was
    created."""
    created = _epoch(2026, 7, 26, 15, 20)
    r = _r(schedule.next_due(created, 2, "day", MELB))
    assert not r.is_pending(_epoch(2026, 7, 27, 23, 59)), "not due until the 28th"
    assert r.is_pending(_epoch(2026, 7, 28, 0, 0)), "should flip at midnight"
    assert r.is_pending(_epoch(2026, 7, 28, 9, 0))


# --- display phrasing ---------------------------------------------------------


def test_humanize_days_speaks_in_days():
    assert humanize_days(0) == "due today"
    assert humanize_days(1) == "due tomorrow"
    assert humanize_days(3) == "in 3 days"
    assert humanize_days(-1) == "1 day overdue"
    assert humanize_days(-3) == "3 days overdue"


def test_humanize_days_coarsens_long_distances():
    assert humanize_days(14) == "in 2 weeks"
    assert humanize_days(90) == "in 3 months"
    assert humanize_days(400) == "in 1 year"
    assert humanize_days(-21) == "3 weeks overdue"


def test_humanize_days_never_mentions_hours():
    # The whole point of the day grid: no sub-day phrasing, at any distance.
    for days in range(-400, 400):
        phrase = humanize_days(days)
        assert "hour" not in phrase and "minute" not in phrase, (days, phrase)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
