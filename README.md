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

Add a reminder from the form at the bottom: give it a name, choose **Repeats** or
**One-time**, and set the cadence ("every 2 weeks" / "in 11 weeks"). Pending items
appear at the top; tap one to mark it done.

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

## Data model

Every reminder has a canonical `due_at` (UTC epoch seconds) — the moment it becomes
pending (`now >= due_at`). Recurring reminders carry an interval `(n, unit)` where
unit is `day | week | month | year`; on completion the timer resets via
`schedule.next_due`. One-off reminders are archived (kept for history, hidden from
lists) on completion. `day`/`week` are fixed-length; `month`/`year` are calendar
hops on the local date with end-of-month clamping (Jan 31 + 1 month → Feb 28/29).

Timestamps are UTC epoch seconds everywhere internally; timezone conversion happens
only in the web layer. See `naggy/schedule.py` (pure, unit-tested) for the maths.

## Web routes

- Pages: `GET /`, `GET /healthz`, `GET /manifest.webmanifest`, `GET /sw.js`
- Read APIs (the Home Assistant seam): `GET /api/reminders`, `GET /api/pending`
- Write APIs: `POST /api/reminders`, `POST /api/reminders/{id}/complete`,
  `PATCH /api/reminders/{id}`, `DELETE /api/reminders/{id}`
- Push: `GET /api/push/key`, `POST /api/push/subscribe`,
  `POST /api/push/unsubscribe`, `POST /api/push/test`

HTMX form posts get back the re-rendered board fragment; the same endpoints answer
JSON when the request isn't from HTMX.

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
- **Quiet hours** — suppress pushes overnight; a pure predicate in `schedule.py`
  taking the local hour, so it stays testable.
- **Home Assistant feed** — surface pending tasks on an HA dashboard by polling the
  existing `GET /api/pending` JSON (or a future `naggy check` that POSTs to HA).

## Notes

- The PWA icons (`naggy/static/icons/`) — an amber square with a white reminder
  bell — are generated by `python tools/make_icons.py` (needs `pip install pillow`).
  Edit that script and re-run to change the artwork.
- `config.toml`, `*.db`, and `docker/.env` are gitignored; only `*.example` files
  are tracked.
