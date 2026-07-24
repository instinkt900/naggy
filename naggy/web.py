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

import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from naggy import __version__, schedule
from naggy.config import load_config
from naggy.constants import INTERVAL_UNITS, KINDS, humanize_delta
from naggy.db import Database
from naggy.models import Reminder

_HERE = Path(__file__).parent


def create_app(config_path: str) -> FastAPI:
    cfg = load_config(config_path)
    db = Database(cfg.database.path)
    db.init_db()
    tz = ZoneInfo(cfg.web.timezone)

    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app = FastAPI(title="Naggy", version=__version__)
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    # --- helpers (closures over cfg/db/tz) ----------------------------------

    def now_epoch() -> int:
        return int(time.time())

    def local_str(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz).strftime("%a %d %b, %H:%M")

    def reminder_json(r: Reminder, now: int) -> dict:
        due_in = r.due_at - now
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
            "due_in_seconds": due_in,
            "human": humanize_delta(due_in),
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
        r = Reminder(
            title=title,
            kind=kind,
            interval_n=interval_n,
            interval_unit=interval_unit,
            due_at=schedule.next_due(now, interval_n, interval_unit, tz),
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

    @app.patch("/api/reminders/{reminder_id}")
    async def patch_reminder(request: Request, reminder_id: int):
        body = await request.json()
        ok = db.update_reminder(reminder_id, body)
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    @app.delete("/api/reminders/{reminder_id}")
    def delete_reminder(request: Request, reminder_id: int):
        ok = db.delete_reminder(reminder_id)
        if wants_partial(request):
            return board_partial(request)
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    return app


def _interval_label(r: Reminder) -> str:
    if not r.interval_n or not r.interval_unit:
        return "one-time"
    unit = r.interval_unit + ("" if r.interval_n == 1 else "s")
    if r.kind == "oneshot":
        return f"once, in {r.interval_n} {unit}"
    return f"every {r.interval_n} {unit}"


def run_serve(config_path: str) -> int:
    """Blocking entrypoint used by `naggy serve` — wires uvicorn to the app."""
    import uvicorn

    cfg = load_config(config_path)
    app = create_app(config_path)
    uvicorn.run(app, host=cfg.web.host, port=cfg.web.port, log_level="info")
    return 0
