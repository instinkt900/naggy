"""SQLite storage.

The `Database` wrapper holds only a path and hands out short-lived connections
(one per operation via `with self.connect()`), each with WAL and foreign keys on.
The whole schema is a single `_SCHEMA` string of `CREATE ... IF NOT EXISTS`
statements; `init_db()` runs it (plus any guarded `ALTER TABLE` migrations) on
every startup, so it is idempotent and forward-only. Rows map to/from the
dataclasses in `models.py` via the `_reminder` / `_completion` helpers.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from naggy import schedule
from naggy.models import Completion, Reminder

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT    NOT NULL,
    notes         TEXT    NOT NULL DEFAULT '',
    kind          TEXT    NOT NULL DEFAULT 'recurring',   -- 'recurring' | 'oneshot'
    interval_n    INTEGER,                                 -- cadence count
    interval_unit TEXT,                                    -- day|week|month|year
    due_at        INTEGER NOT NULL,                         -- epoch s; pending when now>=due_at
    last_done_at  INTEGER,                                  -- epoch s; NULL until first done
    created_at    INTEGER NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(active, due_at);

CREATE TABLE IF NOT EXISTS completions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_id INTEGER NOT NULL REFERENCES reminders(id) ON DELETE CASCADE,
    done_at     INTEGER NOT NULL,
    was_due_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_completions_reminder ON completions(reminder_id, done_at);
"""

# Columns a PATCH is allowed to touch (whitelist guards against arbitrary writes).
_UPDATABLE = {"title", "notes", "kind", "interval_n", "interval_unit", "due_at", "active"}


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self) -> None:
        """Create the schema (and run any additive migrations) — idempotent."""
        parent = Path(self.path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(_SCHEMA)
            # Future notify/HA columns land here via guarded ALTER TABLE, e.g.:
            #   if not self._has_column(conn, "reminders", "notify_channel"):
            #       conn.execute("ALTER TABLE reminders ADD COLUMN notify_channel TEXT")

    # --- reads ---------------------------------------------------------------

    def list_active(self) -> list[Reminder]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE active = 1 ORDER BY due_at"
            ).fetchall()
        return [_reminder(r) for r in rows]

    def get_reminder(self, reminder_id: int) -> Reminder | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
        return _reminder(row) if row else None

    def recent_completions(self, limit: int = 50) -> list[Completion]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM completions ORDER BY done_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_completion(r) for r in rows]

    # --- writes --------------------------------------------------------------

    def add_reminder(self, r: Reminder) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO reminders
                     (title, notes, kind, interval_n, interval_unit,
                      due_at, last_done_at, created_at, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (r.title, r.notes, r.kind, r.interval_n, r.interval_unit,
                 r.due_at, r.last_done_at, r.created_at, int(r.active)),
            )
            return int(cur.lastrowid)

    def complete_reminder(self, reminder_id: int, now: int, tz: ZoneInfo) -> Reminder | None:
        """Address a reminder in one transaction: log the completion, then either
        reset a recurring reminder's timer or archive a one-shot. Returns the
        updated reminder (inactive if archived), or None if it doesn't exist."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
            if row is None:
                return None
            r = _reminder(row)

            conn.execute(
                "INSERT INTO completions (reminder_id, done_at, was_due_at) VALUES (?, ?, ?)",
                (reminder_id, now, r.due_at),
            )

            if r.kind == "recurring" and r.interval_n and r.interval_unit:
                r.due_at = schedule.next_due(now, r.interval_n, r.interval_unit, tz)
                r.last_done_at = now
                conn.execute(
                    "UPDATE reminders SET due_at = ?, last_done_at = ? WHERE id = ?",
                    (r.due_at, r.last_done_at, reminder_id),
                )
            else:
                # One-shot (or a recurring row missing its cadence): archive it.
                r.active = False
                r.last_done_at = now
                conn.execute(
                    "UPDATE reminders SET active = 0, last_done_at = ? WHERE id = ?",
                    (now, reminder_id),
                )
            return r

    def update_reminder(self, reminder_id: int, fields: dict) -> bool:
        cols = {k: v for k, v in fields.items() if k in _UPDATABLE and v is not None}
        if not cols:
            return False
        if "active" in cols:
            cols["active"] = int(bool(cols["active"]))
        assignments = ", ".join(f"{k} = ?" for k in cols)
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE reminders SET {assignments} WHERE id = ?",
                (*cols.values(), reminder_id),
            )
            return cur.rowcount > 0

    def delete_reminder(self, reminder_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            return cur.rowcount > 0


def _reminder(row: sqlite3.Row) -> Reminder:
    return Reminder(
        title=row["title"],
        kind=row["kind"],
        interval_n=row["interval_n"],
        interval_unit=row["interval_unit"],
        due_at=row["due_at"],
        notes=row["notes"],
        last_done_at=row["last_done_at"],
        created_at=row["created_at"],
        active=bool(row["active"]),
        id=row["id"],
    )


def _completion(row: sqlite3.Row) -> Completion:
    return Completion(
        reminder_id=row["reminder_id"],
        done_at=row["done_at"],
        was_due_at=row["was_due_at"],
        id=row["id"],
    )
