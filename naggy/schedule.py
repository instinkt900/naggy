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


def notify_time(due_at: int, tz: ZoneInfo, hour: int, minute: int) -> int:
    """First moment at or after `due_at` whose local clock reads `hour:minute`.

    Lets the nag land at a civilised hour without disturbing the schedule: a
    reminder still *becomes due* at its own anniversary (which is whatever time of
    day it was last addressed), we just hold the notification until the next
    hour:minute. A chore falling due at 13:50 with a 08:00 notify time is pushed
    the following morning, not eighteen hours early.

    Arithmetic is on the local wall clock, so the hour stays put across a DST
    change rather than sliding by an hour.
    """
    local = datetime.fromtimestamp(due_at, tz)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate < local:
        candidate += timedelta(days=1)
    return int(candidate.timestamp())


def due_for_notification(
    reminders: list[Reminder],
    now: int,
    *,
    tz: ZoneInfo | None = None,
    notify_at: tuple[int, int] | None = None,
    repeat: bool = False,
) -> list[Reminder]:
    """Pending reminders that should be pushed about right now.

    `notified_at` records when we last sent a notification. By default a reminder
    is worth nagging about when that stamp is older than the due moment it belongs
    to — which makes "notify at most once per cycle" fall out of the arithmetic
    rather than needing its own bookkeeping: addressing a recurring reminder moves
    `due_at` forward past the old stamp and re-arms it, while an ignored reminder
    keeps a stamp newer than its (unchanged) `due_at` and stays quiet.

    `repeat` drops that once-per-cycle rule so an outstanding chore is re-pushed on
    every pass. Paired with a stable notification tag, that makes a notification
    the user swipes away come back — the closest the web platform gets to
    Android's "ongoing" notifications, which it cannot set.

    `notify_at` (an (hour, minute) pair, needing `tz`) holds each push until that
    local time via `notify_time`.

    Same ordering as `board`: most overdue first.
    """
    out = []
    for r in reminders:
        if not r.is_pending(now):
            continue
        gate = r.due_at
        if notify_at is not None and tz is not None:
            gate = notify_time(r.due_at, tz, *notify_at)
        if now < gate:
            continue
        if not repeat and r.notified_at is not None and r.notified_at >= r.due_at:
            continue
        out.append(r)
    out.sort(key=lambda r: r.due_at)
    return out
