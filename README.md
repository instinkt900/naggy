# Naggy

A small, self-contained **phone-first reminder app for household chores**. Define a
chore and how often it needs doing — "clean bedsheets" every 2 weeks, or a one-off
"harvest carrots" in 11 weeks. When a reminder's timer runs out it shows up under
**Pending actions** at the top of the app. Tap it, confirm, and it's addressed: a
repeating chore resets its timer for next time; a one-off is removed.

Built in the same mold as its sibling app *Sugar Daddy*: FastAPI + Jinja2 + HTMX,
SQLite, a network-first PWA, no frontend build step.

## Stack

- Python **3.11+**. Web: **FastAPI + Jinja2 + HTMX**. Storage: **SQLite** (stdlib
  `sqlite3`, WAL). Runs under **uvicorn**.
- The phone UI is HTMX-driven — every mutation swaps a single re-rendered board
  partial. Static assets (htmx) are **vendored**; there is no build step.
- Tests are **plain-assert** files runnable with bare `python`.

## Quick start

```bash
cd ~/Development/naggy
python -m venv .venv && . .venv/bin/activate
pip install -e .

cp config.example.toml config.toml     # edit timezone if you like
python -m naggy init-db -c config.toml
python -m naggy serve   -c config.toml  # open http://localhost:8080/
```

Add a reminder from the form between the two lists: give it a name and the day it's
first due (today, unless you pick another), then tick **Repeats** and set a cadence
if it should come round again — leave it unticked for a one-off. Pending items
appear at the top; tap one to mark it done, or **long-press** any reminder to edit
its name, date, cadence and notes.

## Commands

```
naggy serve   -c config.toml            # run the web app (uvicorn)
naggy init-db -c config.toml            # create schema and exit
naggy report  -c config.toml [--json]   # print what's pending / upcoming
naggy notify  -c config.toml [--dry-run] # push for anything newly due
naggy vapid-keys                        # mint a Web Push signing key
```

## Notifications

Naggy can push a phone notification when a chore falls due, and it is **its own
push server** — it holds a VAPID keypair and signs and encrypts every message
itself. No third-party notification service, no account, no API key. The browser's
push service (FCM on Android) only relays a blob it cannot read; that relay is part
of the browser and can't be self-hosted, but nothing about your chores is visible
to it.

Setup:

```bash
naggy vapid-keys                        # prints NAGGY_VAPID_PRIVATE_KEY=...
```

Put that in the server's environment (`docker/.env` for the compose deployment),
then set `enabled = true` and your `subject` under `[notify]` in `config.toml` and
restart. On the phone, open the app and tap **Enable notifications**.

Two requirements worth knowing up front:

- **HTTPS is mandatory.** Browsers won't register a service worker — never mind
  subscribe to push — over plain HTTP. Serve Naggy behind a reverse proxy with a
  real certificate.
- **On Android, install the PWA first** (Chrome ⋮ → *Add to Home screen*). Push to
  a plain browser tab is unreliable and iOS refuses it outright.

A running server checks for newly-due reminders every `[notify] poll_seconds` and
pushes; set that to `0` to disable the poller and run `naggy notify` from cron
instead. Either way each reminder is notified **once per due cycle** — completing a
recurring chore re-arms it for the next one. `POST /api/push/test` sends a
throwaway notification to every subscribed device, which is the quickest way to
prove the chain works.

### When a nag actually fires

Reminders are scheduled **by date**, so a chore becomes pending at **local
midnight** on the day it falls due. Nobody wants to be buzzed then, so the push is
held until `notify_at` — `"08:00"` by default — on that same morning. The board and
the notification are deliberately separate: open the app at 00:30 and the chore is
already sitting in Pending actions; the phone just doesn't ring about it until 8am.

Change the hour with `[notify] notify_at = "HH:MM"` (24-hour, local). It's held on
the wall clock, so it survives DST. Setting it to `""` pushes at midnight. If a
pass is missed — server down all morning — the nag still goes out when the server
comes back, rather than waiting for the next day.

### Making a nag hard to ignore

The web platform can't produce a non-dismissible notification — Android's "ongoing"
flag isn't exposed to web apps. Two settings get close:

- `repeat_while_pending = true` re-pushes anything still outstanding on every pass.
  Because every nag shares one notification tag, swiping it away just means it
  reappears within `poll_seconds` instead of stacking up. Reposts are always silent
  and never re-alert — only the first notification of a cycle buzzes.
- `badge = true` puts the pending count on the home-screen app icon via the Badging
  API. That one genuinely can't be swiped away; it clears when the board does. The
  page keeps it in step on every board swap, so completing the last chore clears it
  at once rather than at the next push.

## Data model

Every reminder has a canonical `due_at` (UTC epoch seconds) — the moment it becomes
pending (`now >= due_at`). Recurring reminders carry an interval `(n, unit)` where
unit is `day | week | month | year`; on completion the timer resets via
`schedule.next_due`. One-off reminders are archived (kept for history, hidden from
lists) on completion.

A cadence says how often a chore comes round, not which day it lands on, so the
first `due_at` can be set outright from a picked date (`schedule.due_from_date`)
instead of being derived from the interval. Only the first cycle is anchored that
way — after that the interval is measured from when the chore is actually
addressed, which is the point of "every 2 weeks".

Naggy is **date-grained**: `due_at` is always the local midnight that opens the day
a chore is due, and the interval arithmetic is done on local calendar dates rather
than by adding seconds. All four units are therefore calendar hops — `month`/`year`
additionally clamp the day-of-month when the target month is shorter (Jan 31 +
1 month → Feb 28/29). Doing it this way is what makes a chore turn pending at
midnight instead of at whatever o'clock you last ticked it off, and it also keeps
`day`/`week` honest across a DST change, which a fixed multiple of 86,400 is not.

Timestamps are UTC epoch seconds everywhere internally; timezone conversion happens
only in the web layer, and `[web] timezone` is what decides where midnight falls.
See `naggy/schedule.py` (pure, unit-tested) for the maths.

## Web routes

- Pages: `GET /`, `GET /healthz`, `GET /manifest.webmanifest`, `GET /sw.js`
- Read APIs (the Home Assistant seam): `GET /api/reminders`, `GET /api/pending`
- Write APIs: `POST /api/reminders`, `POST /api/reminders/{id}/complete`,
  `PATCH /api/reminders/{id}`, `DELETE /api/reminders/{id}`
- Push: `GET /api/push/key`, `POST /api/push/subscribe`,
  `POST /api/push/unsubscribe`, `POST /api/push/test`

HTMX form posts get back the re-rendered board fragment; the same endpoints answer
JSON when the request isn't from HTMX. `PATCH` accepts either a form body or JSON,
and every field on it is optional — send `due_date` as `YYYY-MM-DD` to move a
reminder to a particular day.

## Deployment (Docker)

```bash
cd docker
cp ../config.example.toml config.toml   # set database.path = "/data/naggy.db"
docker compose up -d --build            # host port ${NAGGY_PORT:-8090} -> 8080
curl localhost:8090/healthz
```

Code is baked into the image, so a code/template/static change needs a rebuild.
`deploy/install-server.sh` wraps `git pull --ff-only && docker compose up -d --build`.

## Roadmap (designed for, not yet built)

- **Per-reminder notification opt-out** — notifications are currently all-or-nothing
  per device. A `notify` flag per reminder would slot in as a guarded `ALTER TABLE`
  in `db.init_db()` plus a filter in `schedule.due_for_notification`.
- **Home Assistant feed** — surface pending tasks on an HA dashboard by polling the
  existing `GET /api/pending` JSON (or a future `naggy check` that POSTs to HA).

## Notes

- The PWA icons (`naggy/static/icons/`) — an amber square with a white reminder
  bell — are generated by `python tools/make_icons.py` (needs `pip install pillow`).
  Edit that script and re-run to change the artwork.
- `config.toml`, `*.db`, and `docker/.env` are gitignored; only `*.example` files
  are tracked.
