"""Naggy command-line entrypoint.

Subcommands: serve (web app), init-db (create schema and exit), report (print
what's pending/upcoming, text or JSON — also the natural future home for a
`notify` command). Imports are lazy per branch so a quick command doesn't drag in
uvicorn/fastapi.
"""

from __future__ import annotations

import argparse
import json
import logging

from naggy import __version__


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="naggy", description=__doc__)
    parser.add_argument("--version", action="version", version=f"naggy {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the web app")
    p_serve.add_argument("-c", "--config", required=True)

    p_init = sub.add_parser("init-db", help="create the database schema and exit")
    p_init.add_argument("-c", "--config", required=True)

    p_report = sub.add_parser("report", help="print pending and upcoming reminders")
    p_report.add_argument("-c", "--config", required=True)
    p_report.add_argument("--json", action="store_true", help="emit JSON instead of text")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.command == "serve":
        from naggy.web import run_serve
        return run_serve(args.config)

    if args.command == "init-db":
        from naggy.config import load_config
        from naggy.db import Database
        cfg = load_config(args.config)
        Database(cfg.database.path).init_db()
        print(f"initialized database at {cfg.database.path}")
        return 0

    if args.command == "report":
        return _report(args.config, as_json=args.json)

    return 1


def _report(config_path: str, *, as_json: bool) -> int:
    import time
    from zoneinfo import ZoneInfo

    from naggy import schedule
    from naggy.config import load_config
    from naggy.constants import humanize_delta
    from naggy.db import Database

    cfg = load_config(config_path)
    db = Database(cfg.database.path)
    db.init_db()
    tz = ZoneInfo(cfg.web.timezone)
    now = int(time.time())
    b = schedule.board(db.list_active(), now)

    def row(r):
        return {
            "id": r.id,
            "title": r.title,
            "kind": r.kind,
            "due_at": r.due_at,
            "due_in_seconds": r.due_at - now,
            "human": humanize_delta(r.due_at - now),
        }

    if as_json:
        print(json.dumps(
            {"now": now,
             "pending": [row(r) for r in b["pending"]],
             "upcoming": [row(r) for r in b["upcoming"]]},
            indent=2,
        ))
        return 0

    print(f"Pending actions ({len(b['pending'])}):")
    for r in b["pending"]:
        print(f"  • {r.title}  [{humanize_delta(r.due_at - now)}]")
    if not b["pending"]:
        print("  (none)")
    print(f"\nUpcoming ({len(b['upcoming'])}):")
    for r in b["upcoming"]:
        print(f"  • {r.title}  [{humanize_delta(r.due_at - now)}]")
    if not b["upcoming"]:
        print("  (none)")
    return 0
