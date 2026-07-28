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
    due_at: int = 0                     # epoch s at local midnight opening the day it's due
    notes: str = ""
    last_done_at: int | None = None     # epoch s of the last completion, if any
    created_at: int = 0
    active: bool = True                 # one-shots go inactive once addressed
    notified_at: int | None = None      # epoch s of the last push sent for this cycle
    id: int | None = None

    def is_pending(self, now: int) -> bool:
        """Pending == active and its due moment has arrived/passed.

        `due_at` is local midnight (see `schedule.next_due`), so in practice this
        flips over at the start of the day the chore is due — no timezone needed
        here, the day boundary is already baked into the stored timestamp.
        """
        return self.active and now >= self.due_at

    def status_at(self, now: int) -> str:
        return "pending" if self.is_pending(now) else "upcoming"


@dataclass
class PushSubscription:
    """A browser's Web Push subscription, as handed to us by `pushManager.subscribe()`.

    `endpoint` is the browser's stable identity for the subscription (and the push
    service URL we POST to), so it — not the row id — is the natural unique key.
    The two `keys` are what the payload gets encrypted to; without them the push
    service could only deliver an empty wake-up.
    """

    endpoint: str
    p256dh: str
    auth: str
    label: str = ""                     # free-text device hint, so rows are tellable apart
    created_at: int = 0
    last_ok_at: int | None = None
    failures: int = 0                   # consecutive transient delivery failures
    id: int | None = None

    def to_info(self) -> dict:
        """Reshape into the subscription_info dict pywebpush expects."""
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}


@dataclass
class Completion:
    """An append-only log row written each time a reminder is addressed. Kept even
    when a one-shot reminder is archived, so history survives."""

    reminder_id: int
    done_at: int
    was_due_at: int | None = None
    id: int | None = None
