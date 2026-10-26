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
    kind: str = "recurring"            # 'recurring' | 'oneshot' | 'open'
    interval_n: int | None = None       # cadence count, e.g. the 2 in "every 2 weeks"
    interval_unit: str | None = None    # 'day' | 'week' | 'month' | 'year'
    due_at: int = 0                     # epoch s at local midnight opening the day it's due
                                        # (open reminders store the day last done there)
    notes: str = ""
    last_done_at: int | None = None     # epoch s of the last completion, if any
    created_at: int = 0
    active: bool = True                 # one-shots go inactive once addressed
    notified_at: int | None = None      # epoch s of the last push sent for this cycle
    id: int | None = None

    def is_pending(self, now: int) -> bool:
        """Pending == active, scheduled, and its due moment has arrived/passed.

        `due_at` is local midnight (see `schedule.next_due`), so in practice this
        flips over at the start of the day the chore is due — no timezone needed
        here, the day boundary is already baked into the stored timestamp.

        Open-ended reminders never pend: their `due_at` is the day they were last
        done, not a deadline, so the check can't apply to them at all. They are
        excluded here rather than at every call site, which is also what keeps
        them out of the push pass for free.
        """
        return self.active and self.kind != "open" and now >= self.due_at

    def status_at(self, now: int) -> str:
        if self.kind == "open":
            return "open"
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
    prev_last_done_at: int | None = None  # the reminder's last_done_at before this; for undo
    id: int | None = None


@dataclass
class NfcTag:
    """A physical NFC tag linked to a reminder.

    The tag itself holds only a URL, `/t/<token>`; tapping it with a phone opens
    that URL, which ticks the chore off. `token` is random rather than the
    reminder id so a guessable URL can't clear chores, and so one reminder can
    have several tags, each removable on its own.
    """

    token: str
    reminder_id: int
    created_at: int = 0
    last_scan_at: int | None = None
    id: int | None = None
