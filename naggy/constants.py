"""Shared vocabulary and small display helpers.

Kept separate from the domain logic so the allowed enum-ish values live in one
place and can be validated at the web edge (we store these as plain strings, not
Python enums, to keep the DB rows and JSON trivially portable).
"""

from __future__ import annotations

# A reminder repeats forever on a cadence, fires exactly once, or is open-ended:
# it never falls due, it only records how long ago it was last done.
KINDS = ("recurring", "oneshot", "open")

# Interval units a cadence can be expressed in. All four are calendar-based — see
# schedule.next_due, which works on local dates and clamps the day-of-month.
INTERVAL_UNITS = ("day", "week", "month", "year")

# Whole days per fixed-length unit. month/year aren't here because they aren't a
# fixed number of days; they're calendar hops.
_UNIT_DAYS = {"day": 1, "week": 7}


def humanize_ago(days: int) -> str:
    """Render a whole-day age (today minus the last-done date) as a human phrase.

    The back-looking twin of `humanize_days`, for open-ended reminders that have
    no due date and can only be described as "last done N ago". Zero is "today",
    one is "yesterday". A negative days — a last-done date in the future, which
    only a hand-typed edit can produce — clamps to "today" rather than inventing
    an "in N days" for something already done. Used only for display.
    """
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{_coarse_days(days)} ago"


def humanize_days(days: int) -> str:
    """Render a whole-day offset (due date minus today) as a human phrase.

    `days` is what `schedule.days_until` returns: 0 = due today, positive = still
    upcoming, negative = overdue by that many days. Naggy schedules by date, so
    counting down in hours would be false precision *and* misleading — a chore due
    tomorrow shouldn't read "in 6 hours" just because you checked in the evening.
    Used only for display.
    """
    if days == 0:
        return "due today"
    if days == 1:
        return "due tomorrow"
    if days < 0:
        return f"{_coarse_days(-days)} overdue"
    return f"in {_coarse_days(days)}"


def _coarse_days(days: int) -> str:
    """Largest sensible single unit for a positive whole-day count."""
    if days < 14:
        return _plural(days, "day")
    if days < 60:
        return _plural(days // 7, "week")
    if days < 365:
        return _plural(days // 30, "month")
    return _plural(days // 365, "year")


def _plural(n: int, unit: str) -> str:
    return f"{n} {unit}" + ("" if n == 1 else "s")
