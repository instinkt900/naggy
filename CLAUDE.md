# CLAUDE.md — Naggy

Context for working on this repo. Product **display name** is "Naggy"; the
**code/package/CLI/container** name is `naggy` (don't rename those).

## Purpose

A small, self-contained phone-first reminder app for household chores. A reminder
either **repeats** on a cadence ("clean bedsheets" every 2 weeks), fires **once**
("harvest carrots" in 11 weeks), or is **open-ended** — shown in the UI as
**"Last done"** ("water the plants") — with no cadence at all — it records *when the chore last happened* instead of when it is
due. Expired reminders surface under **Pending actions**; tapping one confirms and
"addresses" it — a recurring reminder resets its timer, a one-off is archived, an
open-ended one stamps today as its last-done date. **Long-pressing** any card opens
a dialog that edits every field, the due date included (and deletes it).

Modelled closely on the sibling app *Sugar Daddy* (`~/Development/sugardaddy`) —
reuse its idioms.

## Stack

- Python **3.11+**. Web: **FastAPI + Jinja2 + HTMX**. Storage: **SQLite** (stdlib
  `sqlite3`, WAL). Runs under **uvicorn**. Phone UI is HTMX-driven; static assets
  (htmx) vendored in `naggy/static/`, **no build step**.
- Tests are **plain-assert** files runnable with bare `python` (see
  `tests/test_schedule.py`), also pytest-compatible.

## Layout

```
naggy/
  cli.py         entrypoint: serve | init-db | report | notify | vapid-keys
  __main__.py    `python -m naggy`
  web.py         FastAPI app factory (create_app); all routes as closures; tz helpers
  db.py          SQLite schema + Database wrapper (short-lived connections)
  models.py      dataclasses: Reminder, Completion, PushSubscription
  schedule.py    PURE maths: next_due, advance_due, start_of_day, due_from_date, days_until,
                 days_since, board, due_for_notification, notify_time
  notify.py      Web Push: VAPID keys, pywebpush sending, the notify pass
  config.py      TOML loader; dataclasses; _known() rejects unknown keys; secrets from env only
  constants.py   KINDS, INTERVAL_UNITS, humanize_days, humanize_ago, _coarse_days
  templates/     base.html, phone/index.html, partials/board.html
  static/        style.css, common.js, phone.js, sw.js, htmx.min.js (vendored), icons/
docker/          Dockerfile, docker-compose.yml
deploy/          install-server.sh
tests/           plain-assert tests
config.example.toml   the only tracked config; real config.toml is gitignored
```

## Data model conventions (important)

- **Timestamps are UTC epoch seconds (int) everywhere internally.** Timezone
  conversion happens only in the web layer.
- **Naggy is date-grained.** `due_at` is *always* the local midnight opening the day
  a chore is due, so a reminder turns pending at midnight rather than at whatever
  o'clock it was last addressed. Nothing user-facing should count in hours or
  minutes — use `schedule.days_until` + `constants.humanize_days` on the due side
  (or the mirror `days_since` + `humanize_ago` on the done side), and keep dates
  displayed without a time of day.
- Each reminder has a canonical **`due_at`**; it is *pending* when `now >= due_at`
  and `active`. Recurring: `interval_n` + `interval_unit` (`day|week|month|year`);
  on completion `due_at = schedule.advance_due(due_at, now, n, unit, tz)` — stepped
  from the *scheduled* date, never from `now`, so a late tap doesn't shift the
  weekday (missed cycles are skipped to the next one still ahead); stays active.
  One-shot: `active` set to 0 on completion (archived; completion history kept).
  Open-ended (`kind = "open"`): `due_at` is the **last day the chore was done**, it
  never pends (`Reminder.is_pending` excludes open — and so do the badge count and
  the notify pass, for free), and completing just stamps `due_at` to today and logs
  a completion; the interval columns may hold stale data and are never read.
- A cadence fixes how *often* a chore comes round but not which day it lands on, so
  creation takes a **`start_date`** (`YYYY-MM-DD` from the form's date picker,
  pre-filled with today) that pins the first `due_at` via `schedule.due_from_date`.
  Later cycles keep stepping from that date, whenever the chore is actually
  addressed. Editing a reminder's date is the same conversion
  (`due_date` on the PATCH). Omitting it falls back to one interval from now, which
  is what Naggy did before the picker existed, so old API callers still work.
- **`kind` is a three-way choice, not a tick box**: radios `recurring | oneshot |
  open`, labelled Once / Repeats / Last done (recurring is the default in both
  forms). The kind picks the form's layout: `phone.js` shows only the fields whose
  `data-kinds` lists it and *disables* the rest — a disabled control isn't
  submitted, so a one-off or an open-ended one sends no interval at all rather
  than a stale one. `interval_n`/`interval_unit` are therefore **optional** on `POST`; when
  `kind` is absent entirely it's derived from whether an interval came, so a
  pre-radio request still means what it used to. `kind = recurring` with no
  interval is a 400. An open-ended form without a date means "done today".
- The date field is relabelled per kind (`data-label-<kind>` on the label): "Due
  on" for a one-off, "First due" / "Next due" for a repeating one, "Last done" for
  open-ended — same picker, same `due_date` field, the label is just honest about
  which way the arrow points. The add sheet adds quick-pick chips under it for
  one-offs (forward) and last-done (back), counted from the form's `data-today`
  with the same end-of-month clamp as `schedule.py`.
- A PATCH to a non-recurring `kind` leaves the stored cadence alone (only `kind`
  changes), so switching back restores it — the dialog keeps it in the hidden
  inputs meanwhile. Nothing reads a one-off's or open-ended reminder's interval:
  `_interval_label` says "one-time" / "last done" and `db.complete_reminder`
  archives on `kind != recurring` (open resets `due_at` to today instead).
- An open-ended reminder's interval would be a way of *typing* a past date, so it
  isn't shown back either: `_interval_label` reads "one-time" / "last done"
  regardless of what's stored.
- Open-ended cards read "**last done N ago**" instead of a due phrase:
  `schedule.days_since` counts *dates* back from `due_at` to now (the mirror of
  `days_until`, same local-midnight grain) and `constants.humanize_ago` renders it
  ("today", "yesterday", "2 weeks ago" — coarse buckets via `_coarse_days`). On the
  board they live in their own **"Last done"** section *below* Upcoming and
  sort **oldest last-done first**, so the stalest chore floats up; the section
  renders only when non-empty and re-renders with every board swap.
- **Keep `schedule.py` pure** (no I/O, no wall clock, no config — tz is passed in).
  New deterministic maths goes there and gets covered in `tests/`. All four units
  are calendar hops on the local date (which is also why day/week survive DST);
  month/year additionally clamp the end of month.
- `db.init_db(tz)` takes the timezone because data migrations need to know where
  the day boundary is. Column additions stay guarded by `_has_column`; **row
  rewrites need a `PRAGMA user_version` bump** (`_USER_VERSION` in `db.py`) since
  they leave no trace to detect.

## Web routes (all in `web.py`)

- Pages: `GET /`, `GET /healthz`, `GET /manifest.webmanifest`, `GET /sw.js`
- Read APIs (the **Home Assistant seam**): `GET /api/reminders`, `GET /api/pending`
  (open-ended reminders never appear in `pending` — they have no due date)
- Write APIs: `POST /api/reminders`, `POST /api/reminders/{id}/complete`,
  `PATCH /api/reminders/{id}`, `DELETE /api/reminders/{id}`
- Push: `GET /api/push/key`, `POST /api/push/subscribe`,
  `POST /api/push/unsubscribe`, `POST /api/push/test`
- HTMX form posts return the re-rendered `partials/board.html` fragment (swap
  target `#board`); the same endpoints answer JSON when `HX-Request` is absent.
- `PATCH` takes a form body (what the edit dialog sends — htmx form-encodes PATCH)
  or a JSON one, whichever the `Content-Type` says. Every field is optional:
  `_clean_updates` in `web.py` validates and coerces only the keys present, so an
  absent field means "leave it alone" rather than "blank it".
- The main view is the board only: Pending, Upcoming, Last done, in that order.
  The add form is a bottom-sheet `<dialog id="add-modal">` opened by the fixed +
  button (`.fab`), and like the edit dialog it stays **outside** the swapped
  fragment — inside `partials/board.html` it would throw away half-typed input on
  every completion. A successful add resets the form and closes the sheet;
  Cancel or a backdrop tap closes it and keeps the draft.
- **Pending cards have no ✕**: the whole card is one button that confirms and
  completes, so there's nothing to delete by mistake beside it. Upcoming and
  Last done cards keep their ✕ (with a confirm). Deleting a pending reminder is
  the **Delete reminder** button in the edit dialog.
- The edit dialog is **one** `<dialog>` in `phone/index.html`, outside `#board` so a
  swap can't yank it away mid-edit; a long press fills it from the pressed card's
  `data-*` attributes (no round trip) and `phone.js` fires the PATCH through
  `htmx.ajax` (its DELETE too, sourced from the dialog rather than the form so
  htmx's form validation can't block it). It can't use `hx-patch`: htmx bakes the URL in when it processes the
  element, and the reminder id isn't known until a card is pressed.
- `sw.js` is **network-first**, so code/template/static changes roll out on reload.

## Commands

```
naggy serve   -c config.toml
naggy init-db -c config.toml
naggy report  -c config.toml [--json]
naggy notify  -c config.toml [--dry-run]
naggy vapid-keys
```

## Configuration

One TOML (`config.toml`; template `config.example.toml`). Sections `[database]`
(path), `[web]` (host/port/timezone — also decides where local midnight falls) and
`[notify]` (enabled/subject/poll_seconds/ttl_seconds/notify_at/silent/
repeat_while_pending/badge). `config.py` uses dataclasses +
`_known()` so an **unknown key/section fails loudly**. Secrets come from **env
only**, never the TOML: `NAGGY_VAPID_PRIVATE_KEY` today, `NAGGY_HA_TOKEN` in
future — since they aren't fields of any section, `_known()` rejects them
automatically if someone pastes one into the file.

## Deployment

Docker; code is **baked in via COPY**, so any code/template/static change needs an
**image rebuild** (restart alone won't pick it up). Compose project/container/image
are `naggy`; container listens on **8080 internally**, host port
`${NAGGY_PORT:-8090}`. Data is a named volume `naggy-data` at `/data`;
`docker/config.toml` bind-mounted read-only (set `database.path = "/data/naggy.db"`).
Deploy: `git pull --ff-only && cd docker && docker compose up -d --build`
(wrapped by `deploy/install-server.sh`). Verify: `curl :<port>/healthz`.

## Notifications (Web Push)

Naggy is its **own push application server**: `notify.py` holds a VAPID keypair and
signs/encrypts each message, so no third-party notification service is involved
(the browser's push service only relays an opaque, E2E-encrypted blob). Needs
**HTTPS** and, on Android, the PWA to be **installed**.

- Private key from `NAGGY_VAPID_PRIVATE_KEY` **only**; the public key is *derived*
  from it at startup, never configured, so the two can't drift. `naggy vapid-keys`
  mints one.
- `reminders.notified_at` stamps the last push for the **current due cycle**. The
  once-per-cycle rule falls out of `notified_at < due_at` arithmetic — see
  `schedule.due_for_notification` (pure, tested in `tests/test_notify.py`).
- `notify_at = "HH:MM"` (default `"08:00"`) holds a push until that local time via
  `schedule.notify_time`, **without touching `due_at`** — reminders fall due at
  midnight and the board must turn over then; only the nag waits for a civilised
  hour. Keep those two concerns separate.
- `repeat_while_pending` drops the once-per-cycle rule so a swiped-away
  notification comes back. Reposts are forced `silent` with `renotify: false`;
  only the first notification of a cycle alerts. **Non-dismissible notifications
  are impossible on the web** — Android's ongoing flag isn't exposed — so the
  Badging API (`setAppBadge`, driven by `data-pending` on the board partial and by
  `badge_count` in the push payload) is the real persistent signal.
- The notification's two images are **not interchangeable**. `icon` is the full
  colour artwork in the drawer (`icon-192.png`); `badge` becomes Android's
  status-bar small icon, which is reduced to its **alpha channel and painted
  white** — so it must be `icon-badge-96.png` (bare bell on transparency). Point
  `badge` at any opaque icon and the status bar shows a blank white square.
- Only stamp `notified_at` when a delivery actually succeeded, so a failed pass
  retries rather than silently swallowing the nag.
- 404/410 from a push service means the subscription is permanently dead → prune
  the row. Anything else is transient → bump `failures` and retry.
- Delivery runs either from the in-process poller (`[notify] poll_seconds`, the
  default since the container has no cron) or from `naggy notify` externally.
  Both call `notify.run_pass`.
- `pywebpush`/`cryptography` imports are **lazy** so the plain-assert tests and the
  rest of the CLI work without them installed.

## Goals / roadmap (designed for, not yet built)

- **Per-reminder notification opt-out**: notifications are all-or-nothing per
  device today. A `notify` flag per reminder would be a guarded `ALTER TABLE` plus
  a filter in `schedule.due_for_notification`.
- **Home Assistant feed**: an HA dashboard consuming `GET /api/pending`, or a future
  `naggy check` that POSTs to HA. The JSON read endpoints already exist for this.

## Conventions & guardrails

- **Match the sibling app's style**: docstrings explain the *why*;
  `from __future__ import annotations` + `X | None`; app factory of closures;
  short-lived DB connections; per-model `*_json()` serializers.
- Keep `schedule.py` pure and covered by `tests/`.
- Secrets/data are gitignored (`config.toml`, `*.db`, `docker/.env`); only
  `*.example` files are tracked.
- Icons (`static/icons/`, amber square + white bell) are generated by
  `tools/make_icons.py` (Pillow); re-run it to change them. Bump `__version__`
  when static assets change — it names the SW cache and stamps every asset URL,
  so clients pick them up. Regeneration rewrites PNG metadata even when the
  pixels are unchanged; `git checkout` the icons you didn't mean to touch.
- Personal project; deploy when the user asks.
