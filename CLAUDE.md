# CLAUDE.md — Naggy

Context for working on this repo. Product **display name** is "Naggy"; the
**code/package/CLI/container** name is `naggy` (don't rename those).

## Purpose

A small, self-contained phone-first reminder app for household chores. A reminder
either **repeats** on a cadence ("clean bedsheets" every 2 weeks) or fires **once**
("harvest carrots" in 11 weeks). Expired reminders surface under **Pending
actions**; tapping one confirms and "addresses" it — a recurring reminder resets
its timer, a one-off is archived.

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
  schedule.py    PURE maths: next_due, board, due_for_notification
  notify.py      Web Push: VAPID keys, pywebpush sending, the notify pass
  config.py      TOML loader; dataclasses; _known() rejects unknown keys; secrets from env only
  constants.py   KINDS, INTERVAL_UNITS, humanize_delta
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
- Each reminder has a canonical **`due_at`**; it is *pending* when `now >= due_at`
  and `active`. Recurring: `interval_n` + `interval_unit` (`day|week|month|year`);
  on completion `due_at = schedule.next_due(now, n, unit, tz)`, stays active.
  One-shot: `active` set to 0 on completion (archived; completion history kept).
- **Keep `schedule.py` pure** (no I/O, no wall clock, no config — tz is passed in).
  New deterministic maths goes there and gets covered in `tests/`. day/week are
  fixed-length; month/year are calendar hops with end-of-month clamping.

## Web routes (all in `web.py`)

- Pages: `GET /`, `GET /healthz`, `GET /manifest.webmanifest`, `GET /sw.js`
- Read APIs (the **Home Assistant seam**): `GET /api/reminders`, `GET /api/pending`
- Write APIs: `POST /api/reminders`, `POST /api/reminders/{id}/complete`,
  `PATCH /api/reminders/{id}`, `DELETE /api/reminders/{id}`
- Push: `GET /api/push/key`, `POST /api/push/subscribe`,
  `POST /api/push/unsubscribe`, `POST /api/push/test`
- HTMX form posts return the re-rendered `partials/board.html` fragment (swap
  target `#board`); the same endpoints answer JSON when `HX-Request` is absent.
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
(path), `[web]` (host/port/timezone) and `[notify]`
(enabled/subject/poll_seconds/ttl_seconds). `config.py` uses dataclasses +
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
- **Quiet hours**: suppress overnight pushes via a pure predicate in `schedule.py`.
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
  `tools/make_icons.py` (Pillow); re-run it to change them. Bump the `sw.js` CACHE
  version when static assets change so clients pick them up.
- Personal project; deploy when the user asks.
