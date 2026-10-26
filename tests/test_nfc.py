"""Plain-assert tests for NFC tag scans: what a scan does, and undoing it.

Runnable with a bare interpreter (`python tests/test_nfc.py`) and also
pytest-compatible. `schedule.scan_action` is pure; the tag and undo tests run
against a throwaway SQLite file, since the undo is a row restore that only means
something against real storage. Neither needs FastAPI installed.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naggy import schedule
from naggy.db import Database
from naggy.models import Reminder

DAY = 86_400
MELB = ZoneInfo("Australia/Melbourne")


def _epoch(y, mo, d, h=0, mi=0, tz=MELB):
    return int(datetime(y, mo, d, h, mi, tzinfo=tz).timestamp())


NOW = _epoch(2026, 10, 26, 18, 30)
TODAY = _epoch(2026, 10, 26)


def _db() -> Database:
    db = Database(Path(tempfile.mkdtemp()) / "naggy.db")
    db.init_db(MELB)
    return db


def _add(db, **kw) -> int:
    kw.setdefault("created_at", NOW - 30 * DAY)
    return db.add_reminder(Reminder(**kw))


# --- scan_action ---------------------------------------------------------------


def test_scan_completes_a_pending_chore():
    r = Reminder("sheets", kind="recurring", interval_n=2, interval_unit="week", due_at=TODAY)
    assert schedule.scan_action(r, NOW) == "complete"


def test_scan_leaves_a_chore_that_isnt_due():
    # Brushing past a tag mid-cycle mustn't skip a cycle.
    r = Reminder("sheets", kind="recurring", interval_n=2, interval_unit="week",
                 due_at=TODAY + DAY)
    assert schedule.scan_action(r, NOW) == "not_due"
    one = Reminder("carrots", kind="oneshot", due_at=TODAY + 7 * DAY)
    assert schedule.scan_action(one, NOW) == "not_due"


def test_scan_always_completes_a_last_done_chore():
    # No due date to wait for; even one already done today is done again.
    r = Reminder("plants", kind="open", due_at=TODAY)
    assert schedule.scan_action(r, NOW) == "complete"


def test_scan_of_an_archived_one_shot_does_nothing():
    r = Reminder("carrots", kind="oneshot", due_at=TODAY - DAY, active=False)
    assert schedule.scan_action(r, NOW) == "archived"


# --- tags ------------------------------------------------------------------------


def test_tags_belong_to_their_reminder():
    db = _db()
    a = _add(db, title="a", kind="open", due_at=TODAY)
    b = _add(db, title="b", kind="open", due_at=TODAY)
    t1 = db.add_tag(a, "tok-1", NOW)
    db.add_tag(a, "tok-2", NOW)
    assert db.add_tag(999, "tok-3", NOW) is None
    assert [t.token for t in db.list_tags(a)] == ["tok-1", "tok-2"]
    assert db.list_tags(b) == []
    # A tag can only be removed through the reminder it belongs to.
    assert not db.delete_tag(b, t1)
    assert db.delete_tag(a, t1)
    assert db.get_tag("tok-1") is None


def test_deleting_a_reminder_drops_its_tags():
    db = _db()
    a = _add(db, title="a", kind="open", due_at=TODAY)
    db.add_tag(a, "tok", NOW)
    db.delete_reminder(a)
    assert db.get_tag("tok") is None


# --- undo ----------------------------------------------------------------------


def test_undo_restores_a_recurring_chore():
    db = _db()
    rid = _add(db, title="sheets", kind="recurring", interval_n=2, interval_unit="week",
               due_at=TODAY - DAY, last_done_at=TODAY - 15 * DAY)
    done, cid = db.complete_with_id(rid, NOW, MELB)
    assert done.due_at > NOW
    back = db.undo_completion(cid)
    assert back.due_at == TODAY - DAY and back.last_done_at == TODAY - 15 * DAY
    assert back.is_pending(NOW)
    assert db.get_completion(cid) is None


def test_undo_unarchives_a_one_shot():
    db = _db()
    rid = _add(db, title="carrots", kind="oneshot", due_at=TODAY)
    _, cid = db.complete_with_id(rid, NOW, MELB)
    assert not db.get_reminder(rid).active
    back = db.undo_completion(cid)
    assert back.active and back.last_done_at is None


def test_undo_restores_a_last_done_date():
    db = _db()
    rid = _add(db, title="plants", kind="open", due_at=TODAY - 5 * DAY)
    done, cid = db.complete_with_id(rid, NOW, MELB)
    assert done.due_at == TODAY
    assert db.undo_completion(cid).due_at == TODAY - 5 * DAY


def test_only_the_latest_completion_can_be_undone():
    # Undoing an older one would rewind the chore past what's been done since.
    db = _db()
    rid = _add(db, title="plants", kind="open", due_at=TODAY - 5 * DAY)
    _, first = db.complete_with_id(rid, NOW, MELB)
    _, second = db.complete_with_id(rid, NOW + 60, MELB)
    assert db.undo_completion(first) is None
    assert db.undo_completion(second) is not None
    # ... and once the later one is gone, the earlier is the latest again.
    assert db.undo_completion(first).due_at == TODAY - 5 * DAY
    assert db.undo_completion(first) is None


# --- the notification after a scan -----------------------------------------------


def test_scan_takes_a_chore_out_of_the_notification_and_undo_puts_it_back():
    # The notify pass won't re-push an undone chore (its notified_at still covers
    # the restored cycle), so the scan page has to — via notified_pending.
    db = _db()
    rid = _add(db, title="water plants", kind="recurring", interval_n=1, interval_unit="week",
               due_at=TODAY)
    db.mark_notified(rid, TODAY + 8 * 3600)
    assert [r.id for r in schedule.notified_pending(db.list_active(), NOW)] == [rid]

    _, cid = db.complete_with_id(rid, NOW, MELB)
    assert schedule.notified_pending(db.list_active(), NOW) == []

    db.undo_completion(cid)
    assert [r.id for r in schedule.notified_pending(db.list_active(), NOW)] == [rid]
    assert schedule.due_for_notification(db.list_active(), NOW) == []


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
