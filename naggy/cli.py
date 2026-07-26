"""Naggy command-line entrypoint.

Subcommands: serve (web app), init-db (create schema and exit), report (print
what's pending/upcoming, text or JSON), notify (push for anything newly due),
vapid-keys (mint a push signing key). Imports are lazy per branch so a quick
command doesn't drag in uvicorn/fastapi — or, for `notify`, the crypto stack.
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

    p_notify = sub.add_parser("notify", help="push a notification for newly-due reminders")
    p_notify.add_argument("-c", "--config", required=True)
    p_notify.add_argument("--dry-run", action="store_true",
                          help="show what would be sent without sending or stamping")

    sub.add_parser("vapid-keys", help="generate a VAPID keypair for push notifications")

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

    if args.command == "notify":
        return _notify(args.config, dry_run=args.dry_run)

    if args.command == "vapid-keys":
        return _vapid_keys()

    return 1


def _notify(config_path: str, *, dry_run: bool) -> int:
    import time
    from zoneinfo import ZoneInfo

    from naggy import notify
    from naggy.config import load_config
    from naggy.db import Database

    cfg = load_config(config_path)
    db = Database(cfg.database.path)
    db.init_db()
    tz = ZoneInfo(cfg.web.timezone)

    try:
        result = notify.run_pass(db, cfg, int(time.time()), tz, dry_run=dry_run)
    except notify.PushError as exc:
        print(f"error: {exc}")
        return 2

    if not result["due"]:
        print("nothing newly due")
        return 0

    if dry_run:
        lead = "would notify"
    elif result["notified"]:
        lead = "notified"
    else:
        # Due, but nothing got through — say so plainly rather than implying a
        # delivery. These stay un-stamped and are retried on the next pass.
        lead = "not delivered, will retry"
    print(f"{lead} — {len(result['due'])} due:")
    for title in result["due"]:
        print(f"  • {title}")
    if not dry_run:
        print(f"\nsent {result.get('sent', 0)}/{result.get('subscriptions', 0)} "
              f"subscription(s), pruned {result.get('pruned', 0)}, "
              f"failed {result.get('failed', 0)}")
    return 0


def _vapid_keys() -> int:
    """Mint a keypair. Only the private key is stored — the public one is derived
    from it at runtime, so there is nothing to keep in sync."""
    from naggy import notify

    try:
        private = notify.generate_private_key()
    except notify.PushError as exc:
        print(f"error: {exc}")
        return 2
    print("Add this to the server's environment (never to config.toml):\n")
    print(f"  NAGGY_VAPID_PRIVATE_KEY={private}\n")
    print(f"Derived public key (served at /api/push/key): {notify.public_key_for(private)}")
    return 0


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
