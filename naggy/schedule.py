"""Pure scheduling maths — no I/O, no wall clock, no config.

This is the deterministic heart of Naggy (the analogue of a pure "analysis"
module): given a moment and a cadence it computes the next due time, and given a
set of reminders and "now" it splits them into pending vs upcoming. Everything is
a plain function taking its inputs as arguments (including the timezone), so it is
trivially covered by the plain-assert tests in `tests/`.

Timestamps are UTC epoch seconds, but Naggy schedules by *date*: a chore is due on
a day, not at a time of day. Every due moment is therefore the local midnight that
opens its day, and all interval arithmetic — day and week included — is done on
the local calendar date rather than by adding seconds. That is what makes a chore
turn pending at midnight instead of at whatever o'clock you last ticked it off,
and it keeps day/week intervals honest across a DST change, which a fixed multiple
of 86_400 is not. month/year additionally clamp the day-of-month when the target
month is shorter (Jan 31 + 1 month -> Feb 28 or 29).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from naggy.constants import _UNIT_DAYS
from naggy.models import Reminder


def next_due(from_epoch: int, n: int, unit: str, tz: ZoneInfo) -> int:
    """Return local midnight opening the day `n` `unit`s after `from_epoch`'s date.

    The time of day `from_epoch` happens to carry is deliberately discarded: a
    reminder completed at 23:50 and one completed at 00:10 the next morning are
    both "done today", so both should next fall due on the same date.
    """
    if n < 1:
        raise ValueError(f"interval count must be >= 1, got {n}")

    local = datetime.fromtimestamp(from_epoch, tz).date()

    if unit in _UNIT_DAYS:
        target = local + timedelta(days=n * _UNIT_DAYS[unit])
    elif unit in ("month", "year"):
        months = n if unit == "month" else n * 12
        total = (local.year * 12 + (local.month - 1)) + months
        year, month = divmod(total, 12)
        month += 1
        target = date(year, month, min(local.day, _days_in_month(year, month)))
    else:
        raise ValueError(f"unknown interval unit: {unit!r}")

    return start_of_day_on(target, tz)


def start_of_day(epoch: int, tz: ZoneInfo) -> int:
    """Local midnight opening the day `epoch` falls in.

    Used to pull a legacy time-of-day `due_at` back onto the day grid (see the
    migration in `db.init_db`).
    """
    return start_of_day_on(datetime.fromtimestamp(epoch, tz).date(), tz)


def start_of_day_on(day: date, tz: ZoneInfo) -> int:
    """Epoch seconds at local midnight opening `day`."""
    return int(datetime(day.year, day.month, day.day, tzinfo=tz).timestamp())


def days_until(due_at: int, now: int, tz: ZoneInfo) -> int:
    """Whole local days from today's date to the due date.

    0 means due today, 1 tomorrow, negative means overdue by that many days. This
    is a *date* subtraction, not a seconds one, so it doesn't matter what time of
    day you look: something due tomorrow reads as one day away at 09:00 and at
    23:00 alike.
    """
    due_day = datetime.fromtimestamp(due_at, tz).date()
    today = datetime.fromtimestamp(now, tz).date()
    return (due_day - today).days


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

    Lets the nag land at a civilised hour without disturbing the schedule. A
    reminder becomes due at midnight — that's the day grid — and nobody wants to
    be buzzed then, so the notification is held until the morning of the day it
    falls due. Kept separate from `due_at` rather than folded into it so the board
    still turns over at midnight; only the push waits.

    Arithmetic is on the local wall clock, so the hour stays put across a DST
    change rather than sliding by an hour. A `due_at` that isn't midnight (a row
    predating the day grid, say) slips to the following morning if the hour has
    already gone by, which is the safe direction — never earlier than due.
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
