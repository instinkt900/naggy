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
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naggy import notify, schedule
from naggy.models import Reminder

DAY = 86_400
NOW = 1_800_000_000
MELB = ZoneInfo("Australia/Melbourne")


def _epoch(y, mo, d, h, mi, tz=MELB):
    return int(datetime(y, mo, d, h, mi, tzinfo=tz).timestamp())


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


# --- notify_at: holding the push until a civilised hour ----------------------


def test_notify_time_waits_for_the_next_morning():
    # Due 13:50 Tuesday with an 08:00 notify time -> pushed Wednesday morning.
    due = _epoch(2026, 7, 28, 13, 50)
    got = schedule.notify_time(due, MELB, 8, 0)
    d = datetime.fromtimestamp(got, MELB)
    assert (d.day, d.hour, d.minute) == (29, 8, 0), d


def test_notify_time_same_day_when_the_hour_is_still_ahead():
    due = _epoch(2026, 7, 28, 6, 15)
    got = schedule.notify_time(due, MELB, 8, 0)
    d = datetime.fromtimestamp(got, MELB)
    assert (d.day, d.hour, d.minute) == (28, 8, 0), d


def test_notify_time_exactly_on_the_hour_does_not_slip_a_day():
    due = _epoch(2026, 7, 28, 8, 0)
    assert schedule.notify_time(due, MELB, 8, 0) == due


def test_notify_time_holds_the_wall_clock_hour_across_dst():
    # Melbourne leaves DST on 5 Apr 2026; the nag must stay at 08:00 local, which
    # means a different UTC offset either side of the change.
    before = schedule.notify_time(_epoch(2026, 4, 3, 20, 0), MELB, 8, 0)
    after = schedule.notify_time(_epoch(2026, 4, 6, 20, 0), MELB, 8, 0)
    for got in (before, after):
        d = datetime.fromtimestamp(got, MELB)
        assert (d.hour, d.minute) == (8, 0), d


def test_reminder_stays_quiet_until_its_notify_time():
    due = _epoch(2026, 7, 28, 13, 50)
    r = _r("sheets", due)
    at_due = schedule.due_for_notification([r], due, tz=MELB, notify_at=(8, 0))
    assert at_due == [], "should be held until 08:00"
    next_morning = _epoch(2026, 7, 29, 8, 0)
    assert len(schedule.due_for_notification([r], next_morning, tz=MELB, notify_at=(8, 0))) == 1


def test_no_notify_at_pushes_as_soon_as_due():
    due = _epoch(2026, 7, 28, 13, 50)
    assert len(schedule.due_for_notification([_r("sheets", due)], due)) == 1


# --- repeat_while_pending ----------------------------------------------------


def test_repeat_re_selects_an_already_notified_reminder():
    r = _r("sheets", due_at=NOW - DAY, notified_at=NOW - DAY + 60)
    assert schedule.due_for_notification([r], NOW) == []
    assert len(schedule.due_for_notification([r], NOW, repeat=True)) == 1


def test_repeat_still_respects_the_notify_time():
    # Repeating must not become a licence to nag before the chosen hour.
    due = _epoch(2026, 7, 28, 13, 50)
    r = _r("sheets", due, notified_at=due - 30 * DAY)
    got = schedule.due_for_notification([r], due, tz=MELB, notify_at=(8, 0), repeat=True)
    assert got == [], got


def test_repeat_does_not_resurrect_upcoming_or_archived():
    upcoming = _r("later", NOW + DAY, notified_at=NOW - DAY)
    archived = _r("done", NOW - DAY, notified_at=NOW - DAY, active=False)
    assert schedule.due_for_notification([upcoming, archived], NOW, repeat=True) == []


# --- payload shaping ---------------------------------------------------------


def test_repost_is_silent_and_does_not_realert():
    p = notify.build_payload([_r("sheets", NOW - DAY)], NOW, repost=True)
    assert p["silent"] is True, p
    assert p["renotify"] is False, p


def test_first_notification_alerts_unless_configured_silent():
    p = notify.build_payload([_r("sheets", NOW - DAY)], NOW)
    assert p["silent"] is False and p["renotify"] is True, p
    quiet = notify.build_payload([_r("sheets", NOW - DAY)], NOW, silent=True)
    assert quiet["silent"] is True and quiet["renotify"] is True, quiet


def test_badge_count_defaults_to_the_batch_but_can_be_overridden():
    rems = [_r("a", NOW - DAY), _r("b", NOW - 10)]
    assert notify.build_payload(rems, NOW)["badge_count"] == 2
    # The true pending total can exceed the handful being pushed this pass.
    assert notify.build_payload(rems, NOW, badge_count=5)["badge_count"] == 5


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
