"""Plain-assert tests for the notification selection rules and payload shaping.

Runnable with a bare interpreter (`python tests/test_notify.py`) and also
pytest-compatible. Only the deterministic parts are covered: which reminders are
worth nagging about (`schedule.due_for_notification`) and how they collapse into
one notification (`notify.build_payload`). Neither touches the network, the clock,
or the crypto stack, so no mocks are needed — and `notify`'s heavyweight imports
are lazy, so this file imports cleanly without pywebpush installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naggy import notify, schedule
from naggy.models import Reminder

DAY = 86_400
NOW = 1_800_000_000


def _r(title, due_at, notified_at=None, active=True, notes=""):
    return Reminder(title=title, due_at=due_at, notified_at=notified_at,
                    active=active, notes=notes)


def test_never_notified_pending_is_selected():
    got = schedule.due_for_notification([_r("sheets", NOW - 10)], NOW)
    assert [r.title for r in got] == ["sheets"], got


def test_upcoming_is_not_selected():
    assert schedule.due_for_notification([_r("sheets", NOW + 10)], NOW) == []


def test_already_notified_this_cycle_stays_quiet():
    # Stamped after it fell due -> we've already nagged about this cycle.
    r = _r("sheets", due_at=NOW - DAY, notified_at=NOW - DAY + 60)
    assert schedule.due_for_notification([r], NOW) == []


def test_completing_a_recurring_reminder_rearms_it():
    # Completion pushes due_at past the old stamp, so the next cycle nags again.
    r = _r("sheets", due_at=NOW - 10, notified_at=NOW - 15 * DAY)
    assert [x.title for x in schedule.due_for_notification([r], NOW)] == ["sheets"]


def test_stamp_exactly_at_due_counts_as_notified():
    # notified_at == due_at means the send happened for this cycle, not the last.
    r = _r("sheets", due_at=NOW - DAY, notified_at=NOW - DAY)
    assert schedule.due_for_notification([r], NOW) == []


def test_archived_reminders_are_never_notified():
    r = _r("carrots", due_at=NOW - DAY, active=False)
    assert schedule.due_for_notification([r], NOW) == []


def test_selection_is_most_overdue_first():
    rems = [
        _r("recent", NOW - 10),
        _r("ancient", NOW - 40 * DAY),
        _r("later", NOW + DAY),
        _r("middling", NOW - DAY),
    ]
    got = [r.title for r in schedule.due_for_notification(rems, NOW)]
    assert got == ["ancient", "middling", "recent"], got


def test_single_payload_uses_the_reminder_title():
    p = notify.build_payload([_r("Clean bedsheets", NOW - 2 * DAY)], NOW)
    assert p["title"] == "Clean bedsheets", p
    assert "overdue" in p["body"], p
    assert p["tag"] == "naggy-pending", p


def test_single_payload_appends_notes():
    p = notify.build_payload([_r("Clean bedsheets", NOW - 10, notes="Hot wash")], NOW)
    assert p["body"].endswith("Hot wash"), p


def test_grouped_payload_counts_and_lists():
    rems = [_r("sheets", NOW - DAY), _r("bins", NOW - 10), _r("filter", NOW - 5)]
    p = notify.build_payload(rems, NOW)
    assert p["title"] == "3 chores need doing", p
    assert p["body"] == "sheets, bins, filter", p


def test_payload_body_is_bounded():
    rems = [_r("chore number %d" % i, NOW - 10) for i in range(200)]
    p = notify.build_payload(rems, NOW)
    assert len(p["body"]) <= 300, len(p["body"])


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
