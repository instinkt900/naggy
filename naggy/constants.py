"""Shared vocabulary and small display helpers.

Kept separate from the domain logic so the allowed enum-ish values live in one
place and can be validated at the web edge (we store these as plain strings, not
Python enums, to keep the DB rows and JSON trivially portable).
"""

from __future__ import annotations

# A reminder either repeats forever on a cadence or fires exactly once.
KINDS = ("recurring", "oneshot")

# Interval units a cadence can be expressed in. day/week are fixed-length; month
# and year are calendar-based (see schedule.next_due for the clamping rules).
INTERVAL_UNITS = ("day", "week", "month", "year")

_UNIT_SECONDS = {"day": 86_400, "week": 604_800}


def humanize_delta(seconds: int) -> str:
    """Render a signed second-delta as a human phrase, e.g. "in 3 days" or
    "2 days overdue". `seconds` is (due_at - now): positive = still upcoming,
    zero/negative = pending. Used only for display."""
    if seconds <= 0:
        overdue = -seconds
        if overdue < 60:
            return "due now"
        return f"{_coarse(overdue)} overdue"
    if seconds < 60:
        return "due in under a minute"
    return f"in {_coarse(seconds)}"


def _coarse(seconds: int) -> str:
    """Largest sensible single unit for a positive duration."""
    minutes = seconds // 60
    if minutes < 60:
        return _plural(minutes, "minute")
    hours = minutes // 60
    if hours < 24:
        return _plural(hours, "hour")
    days = hours // 24
    if days < 14:
        return _plural(days, "day")
    if days < 60:
        return _plural(days // 7, "week")
    if days < 365:
        return _plural(days // 30, "month")
    return _plural(days // 365, "year")


def _plural(n: int, unit: str) -> str:
    return f"{n} {unit}" + ("" if n == 1 else "s")
