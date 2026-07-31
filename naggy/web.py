"""FastAPI web layer.

`create_app()` is a factory: it loads config, opens the database (running
migrations), resolves the display timezone, and defines every route as a closure
capturing `cfg`/`db`/`tz` — no module globals, so the app is trivial to build in a
test. Storage stays UTC epoch seconds; conversion to the configured timezone
happens only in this file.

The phone UI is HTMX-driven: mutations POST a form and get back the re-rendered
`partials/board.html` fragment to swap in. The same endpoints answer JSON when the
request isn't from HTMX, and the read endpoints (`/api/reminders`, `/api/pending`)
are the stable JSON seam a future Home Assistant dashboard would poll.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from naggy import __version__, notify, schedule
from naggy.config import load_config
from naggy.constants import INTERVAL_UNITS, KINDS, humanize_days
from naggy.db import Database
from naggy.models import Reminder

_HERE = Path(__file__).parent

log = logging.getLogger(__name__)


def create_app(config_path: str) -> FastAPI:
    cfg = load_config(config_path)
    tz = ZoneInfo(cfg.web.timezone)
    db = Database(cfg.database.path)
    db.init_db(tz)

    # Derived once at startup: "" means push is off or misconfigured, which the UI
    # reads as "notifications unavailable" rather than an error.
    vapid_public_key = notify.public_key(cfg)
    if cfg.notify.enabled and not vapid_public_key:
        log.warning("[notify] is enabled but no usable VAPID key — push is disabled")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        """Run the notify pass on a timer inside the server process.

        The Docker deployment has no cron, so polling here is what makes push work
        out of the box; setting `poll_seconds = 0` disables it for anyone who'd
        rather drive `naggy notify` externally. The pass does blocking network I/O,
        hence `to_thread` — it must not stall the event loop serving the board.
        """
        task = None
        if vapid_public_key and cfg.notify.poll_seconds > 0:
            task = asyncio.create_task(_poll_loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _poll_loop() -> None:
        while True:
            await asyncio.sleep(cfg.notify.poll_seconds)
            try:
                result = await asyncio.to_thread(
                    notify.run_pass, db, cfg, now_epoch(), tz
                )
                if result["sent"]:
                    log.info("notify pass: %s", result)
            except Exception:  # a bad pass must never kill the loop
                log.exception("notify pass failed")

    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    # Every template stamps `?v={{ v }}` onto its asset URLs, so a release always
    # reaches clients even if something is holding an old copy.
    templates.env.globals["v"] = __version__
    app = FastAPI(title="Naggy", version=__version__, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    @app.middleware("http")
    async def revalidate_static(request: Request, call_next):
        """Force the browser to revalidate every static asset.

        StaticFiles sends ETag/Last-Modified but no Cache-Control, which leaves
        Chrome free to reuse a heuristically-fresh copy without asking. Android
        PWAs hit this hard: a reinstalled app kept running JS it had cached before
        the deploy, and because the service worker's own `fetch()` reads the same
        HTTP cache, bumping the SW cache version just re-pinned the stale file.
        `no-cache` still allows caching — it only requires an ETag check first, so
        the usual answer is a cheap 304.
        """
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # --- helpers (closures over cfg/db/tz) ----------------------------------

    def now_epoch() -> int:
        return int(time.time())

    def local_str(ts: int) -> str:
        # Date only: reminders are due on a day, so showing a time of day would
        # imply a precision the schedule doesn't have.
        return datetime.fromtimestamp(ts, tz).strftime("%a %d %b")

    def local_date(ts: int) -> str:
        # The machine-readable twin of `local_str`, in the one format
        # `<input type="date">` will accept — it's what fills the edit modal.
        return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")

    def reminder_json(r: Reminder, now: int) -> dict:
        due_in = r.due_at - now
        due_in_days = schedule.days_until(r.due_at, now, tz)
        return {
            "id": r.id,
            "title": r.title,
            "notes": r.notes,
            "kind": r.kind,
            "interval_n": r.interval_n,
            "interval_unit": r.interval_unit,
            "interval_label": _interval_label(r),
            "due_at": r.due_at,
            "due_at_ms": r.due_at * 1000,
            "due_local": local_str(r.due_at),
            "due_date": local_date(r.due_at),
            # Both grains are published: `due_in_days` is what Naggy actually
            # schedules on and what the UI shows, `due_in_seconds` stays for any
            # consumer of the JSON seam that wants the raw distance.
            "due_in_days": due_in_days,
            "due_in_seconds": due_in,
            "human": humanize_days(due_in_days),
            "status": r.status_at(now),
        }

    def board_view(now: int) -> dict:
        b = schedule.board(db.list_active(), now)
        return {
            "pending": [reminder_json(r, now) for r in b["pending"]],
            "upcoming": [reminder_json(r, now) for r in b["upcoming"]],
        }

    def wants_partial(request: Request) -> bool:
        return request.headers.get("HX-Request") == "true"

    def board_partial(request: Request) -> Response:
        now = now_epoch()
        return templates.TemplateResponse(
            request, "partials/board.html", {"board": board_view(now)}
        )

    # --- pages ---------------------------------------------------------------

    @app.get("/")
    def index(request: Request):
        now = now_epoch()
        return templates.TemplateResponse(
            request,
            "phone/index.html",
            {
                "board": board_view(now),
                "kinds": KINDS,
                "units": INTERVAL_UNITS,
            },
        )

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "reminders": len(db.list_active())}

    @app.get("/manifest.webmanifest")
    def manifest():
        return JSONResponse(
            {
                "name": "Naggy",
                "short_name": "Naggy",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#12141a",
                "theme_color": "#12141a",
                "icons": [
                    {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                    {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
                    {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512",
                     "type": "image/png", "purpose": "maskable"},
                ],
            },
            media_type="application/manifest+json",
        )

    @app.get("/sw.js")
    def service_worker():
        # Served from root so its scope covers the whole app; never cached.
        return FileResponse(
            _HERE / "static" / "sw.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    # --- read APIs (Home Assistant seam) -------------------------------------

    @app.get("/api/reminders")
    def api_reminders():
        now = now_epoch()
        return {"now": now, "reminders": [reminder_json(r, now) for r in db.list_active()]}

    @app.get("/api/pending")
    def api_pending():
        now = now_epoch()
        return {"now": now, "pending": board_view(now)["pending"]}

    # --- write APIs ----------------------------------------------------------

    @app.post("/api/reminders")
    def create_reminder(
        request: Request,
        title: str = Form(...),
        kind: str = Form("recurring"),
        interval_n: int = Form(...),
        interval_unit: str = Form(...),
        notes: str = Form(""),
        start_date: str = Form(""),
    ):
        title = title.strip()
        if not title:
            return JSONResponse({"error": "title is required"}, status_code=400)
        if kind not in KINDS:
            kind = "recurring"
        if interval_unit not in INTERVAL_UNITS:
            return JSONResponse({"error": f"bad interval_unit: {interval_unit}"}, status_code=400)
        if interval_n < 1:
            return JSONResponse({"error": "interval must be >= 1"}, status_code=400)

        now = now_epoch()
        # A cadence says how *often* a chore comes round but not which day it lands
        # on, so an optional start date pins the first one; without it the first due
        # day is one interval from today, as it always was. Only the first cycle is
        # anchored — later ones are measured from when the chore is actually
        # addressed, which is the point of an "every 2 weeks" reminder.
        start_date = start_date.strip()
        try:
            due_at = (
                schedule.due_from_date(start_date, tz) if start_date
                else schedule.next_due(now, interval_n, interval_unit, tz)
            )
        except ValueError:
            return JSONResponse({"error": f"bad start date: {start_date}"}, status_code=400)

        r = Reminder(
            title=title,
            kind=kind,
            interval_n=interval_n,
            interval_unit=interval_unit,
            due_at=due_at,
            notes=notes.strip(),
            created_at=now,
        )
        r.id = db.add_reminder(r)

        if wants_partial(request):
            return board_partial(request)
        return reminder_json(r, now)

    @app.post("/api/reminders/{reminder_id}/complete")
    def complete_reminder(request: Request, reminder_id: int):
        now = now_epoch()
        r = db.complete_reminder(reminder_id, now, tz)
        if r is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if wants_partial(request):
            return board_partial(request)
        return reminder_json(r, now)

    # --- push subscriptions ---------------------------------------------------

    @app.get("/api/push/key")
    def push_key():
        """The application server key the browser needs to subscribe.

        503 (rather than an error page) is the UI's signal to show the toggle as
        unavailable — it distinguishes "push isn't set up on this server" from
        "this browser can't do push".
        """
        if not vapid_public_key:
            return JSONResponse({"error": "push not configured"}, status_code=503)
        return {"key": vapid_public_key}

    @app.post("/api/push/subscribe")
    async def push_subscribe(request: Request):
        body = await request.json()
        endpoint = (body.get("endpoint") or "").strip()
        keys = body.get("keys") or {}
        p256dh = (keys.get("p256dh") or "").strip()
        auth = (keys.get("auth") or "").strip()
        if not endpoint or not p256dh or not auth:
            return JSONResponse({"error": "malformed subscription"}, status_code=400)
        sub_id = db.add_subscription(
            endpoint, p256dh, auth, (body.get("label") or "").strip()[:80], now_epoch()
        )
        return {"ok": True, "id": sub_id}

    @app.post("/api/push/unsubscribe")
    async def push_unsubscribe(request: Request):
        body = await request.json()
        ok = db.delete_subscription((body.get("endpoint") or "").strip())
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    @app.post("/api/push/test")
    async def push_test():
        """Send a throwaway notification — the only way to prove the whole chain
        (VAPID signing, push service, service worker) works without waiting for a
        chore to fall due."""
        if not vapid_public_key:
            return JSONResponse({"error": "push not configured"}, status_code=503)
        payload = {
            "title": "Naggy",
            "body": "Test notification — push is working.",
            "url": "/",
            "tag": "naggy-test",
        }
        result = await asyncio.to_thread(
            notify.send_to_all, db, cfg, payload, now_epoch()
        )
        return result

    @app.patch("/api/reminders/{reminder_id}")
    async def patch_reminder(request: Request, reminder_id: int):
        """Edit an existing reminder — what the long-press modal saves.

        Accepts either shape: the modal posts a form (htmx sends PATCH bodies
        form-encoded), an API client posts JSON. Every field is optional, so a
        caller can nudge one thing without restating the rest.
        """
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("application/json"):
            raw = await request.json()
        else:
            raw = dict(await request.form())

        fields, error = _clean_updates(raw, tz)
        if error:
            return JSONResponse({"error": error}, status_code=400)
        if not fields:
            return JSONResponse({"error": "nothing to update"}, status_code=400)
        if not db.update_reminder(reminder_id, fields):
            return JSONResponse({"error": "not found"}, status_code=404)

        if wants_partial(request):
            return board_partial(request)
        now = now_epoch()
        return reminder_json(db.get_reminder(reminder_id), now)

    @app.delete("/api/reminders/{reminder_id}")
    def delete_reminder(request: Request, reminder_id: int):
        ok = db.delete_reminder(reminder_id)
        if wants_partial(request):
            return board_partial(request)
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    return app


def _interval_label(r: Reminder) -> str:
    # A one-shot's interval was only ever a way of *typing* its due date — and the
    # date can now be picked outright or edited afterwards, so quoting the original
    # cadence back ("once, in 11 weeks") would describe how the reminder was
    # created rather than when it's due. The card already shows the date itself.
    if r.kind == "oneshot" or not r.interval_n or not r.interval_unit:
        return "one-time"
    unit = r.interval_unit + ("" if r.interval_n == 1 else "s")
    return f"every {r.interval_n} {unit}"


def _clean_updates(raw: dict, tz: ZoneInfo) -> tuple[dict, str | None]:
    """Normalise a partial reminder update into columns `db.update_reminder` takes.

    Only keys actually present are returned, so an absent field means "leave it
    alone" rather than "blank it". Values arrive as strings from a form and as
    real types from JSON, hence the coercions. Returns `(fields, error)`; a
    non-None error is a 400 and nothing is written.
    """
    out: dict = {}

    if "title" in raw:
        title = str(raw["title"]).strip()
        if not title:
            return {}, "title is required"
        out["title"] = title

    if "notes" in raw:
        out["notes"] = str(raw["notes"]).strip()

    if "kind" in raw:
        kind = str(raw["kind"])
        if kind not in KINDS:
            return {}, f"bad kind: {kind}"
        out["kind"] = kind

    if "interval_unit" in raw:
        unit = str(raw["interval_unit"])
        if unit not in INTERVAL_UNITS:
            return {}, f"bad interval_unit: {unit}"
        out["interval_unit"] = unit

    if "interval_n" in raw:
        try:
            n = int(raw["interval_n"])
        except (TypeError, ValueError):
            return {}, "interval must be a whole number"
        if n < 1:
            return {}, "interval must be >= 1"
        out["interval_n"] = n

    # `due_date` is the grain the UI works in (a day the user picked); `due_at`
    # stays accepted for callers that already speak epoch seconds.
    if raw.get("due_date"):
        try:
            out["due_at"] = schedule.due_from_date(str(raw["due_date"]), tz)
        except ValueError:
            return {}, f"bad date: {raw['due_date']}"
    elif raw.get("due_at") is not None:
        try:
            out["due_at"] = int(raw["due_at"])
        except (TypeError, ValueError):
            return {}, "due_at must be epoch seconds"

    if "active" in raw:
        out["active"] = _truthy(raw["active"])

    return out, None


def _truthy(value: object) -> bool:
    """Form checkboxes and JSON booleans, read the same way.

    A form can only ever send strings, so `"0"`/`"false"` have to be spelled out —
    `bool("false")` is True, which would make un-archiving impossible.
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def run_serve(config_path: str) -> int:
    """Blocking entrypoint used by `naggy serve` — wires uvicorn to the app."""
    import uvicorn

    cfg = load_config(config_path)
    app = create_app(config_path)
    uvicorn.run(app, host=cfg.web.host, port=cfg.web.port, log_level="info")
    return 0
