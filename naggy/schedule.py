"""Pure scheduling maths — no I/O, no wall clock, no config.

This is the deterministic heart of Naggy (the analogue of a pure "analysis"
module): given a moment and a cadence it computes the next due time, and given a
set of reminders and "now" it splits them into pending vs upcoming. Everything is
a plain function taking its inputs as arguments (including the timezone), so it is
trivially covered by the plain-assert tests in `tests/`.

Timestamps are UTC epoch seconds. day/week intervals are fixed-length additions.
month/year intervals are *calendar* additions performed on the local date so that
"every month" lands on the same day-of-month in the configured timezone, clamping
to the last valid day when the target month is shorter (Jan 31 + 1 month -> Feb 28
or 29).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from naggy.constants import _UNIT_SECONDS
from naggy.models import Reminder


def next_due(from_epoch: int, n: int, unit: str, tz: ZoneInfo) -> int:
    """Return the epoch-second timestamp `n` `unit`s after `from_epoch`.

    day/week are exact multiples of seconds. month/year are calendar hops on the
    local date, preserving the time-of-day and clamping the day-of-month.
    """
    if n < 1:
        raise ValueError(f"interval count must be >= 1, got {n}")

    if unit in _UNIT_SECONDS:
        return from_epoch + n * _UNIT_SECONDS[unit]

    if unit not in ("month", "year"):
        raise ValueError(f"unknown interval unit: {unit!r}")

    local = datetime.fromtimestamp(from_epoch, tz)
    months = n if unit == "month" else n * 12
    total = (local.year * 12 + (local.month - 1)) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(local.day, _days_in_month(year, month))
    shifted = local.replace(year=year, month=month, day=day)
    return int(shifted.timestamp())


def _days_in_month(year: int, month: int) -> int:
    # First day of the next month minus one day = last day of this month.
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


def board(reminders: list[Reminder], now: int) -> dict[str, list[Reminder]]:
    """Split active reminders into the two lists the UI shows.

    `pending`: due moment reached — most overdue first (they need attention most).
    `upcoming`: not yet due — soonest first (what's coming next).
    Inactive reminders (archived one-shots) are excluded from both.
    """
    pending: list[Reminder] = []
    upcoming: list[Reminder] = []
    for r in reminders:
        if not r.active:
            continue
        (pending if r.is_pending(now) else upcoming).append(r)
    pending.sort(key=lambda r: r.due_at)          # most overdue (smallest due_at) first
    upcoming.sort(key=lambda r: r.due_at)          # soonest first
    return {"pending": pending, "upcoming": upcoming}
