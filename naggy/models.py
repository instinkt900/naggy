"""Row dataclasses shared across the db, web, and schedule layers.

Plain dataclasses (no pydantic). Required fields first, `id: int | None = None`
last so a row can be built in memory before it is persisted. All timestamps are
UTC epoch seconds (int); display conversion happens only at the web edge.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Reminder:
    title: str
    kind: str = "recurring"            # 'recurring' | 'oneshot'
    interval_n: int | None = None       # cadence count, e.g. the 2 in "every 2 weeks"
    interval_unit: str | None = None    # 'day' | 'week' | 'month' | 'year'
    due_at: int = 0                     # epoch s; the moment it becomes pending
    notes: str = ""
    last_done_at: int | None = None     # epoch s of the last completion, if any
    created_at: int = 0
    active: bool = True                 # one-shots go inactive once addressed
    id: int | None = None

    def is_pending(self, now: int) -> bool:
        """Pending == active and its due moment has arrived/passed."""
        return self.active and now >= self.due_at

    def status_at(self, now: int) -> str:
        return "pending" if self.is_pending(now) else "upcoming"


@dataclass
class Completion:
    """An append-only log row written each time a reminder is addressed. Kept even
    when a one-shot reminder is archived, so history survives."""

    reminder_id: int
    done_at: int
    was_due_at: int | None = None
    id: int | None = None
