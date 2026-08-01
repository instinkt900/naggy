"""Web Push notifications — Naggy is its own application server.

Naggy pushes straight from this box: it holds a VAPID keypair and signs every
message itself. The browser's push service (FCM on Android) only *relays* an
already-encrypted blob, so there is no third-party notification account in the
loop and the relay never sees the payload — it is encrypted end-to-end under keys
derived per subscription (RFC 8291). That is as self-hosted as the web platform
allows; the relay itself cannot be swapped out, it is baked into the browser.

The private key is a secret, so it comes from `NAGGY_VAPID_PRIVATE_KEY` only,
never the TOML (see `config.py`). The matching public key is *derived* from it
rather than configured separately, so the two can never drift apart and a rotated
key takes effect everywhere at once. Generate one with `naggy vapid-keys`.

Imports of pywebpush/cryptography are lazy per function so the rest of the app —
and the plain-assert tests, which run under a bare interpreter — keep working
without those wheels installed. Selection of *which* reminders to nag about is
deliberately not here: it is pure maths and lives in `schedule.due_for_notification`.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import TYPE_CHECKING

from naggy import schedule
from naggy.constants import humanize_days

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps imports lazy at runtime
    from zoneinfo import ZoneInfo

    from naggy.config import Config
    from naggy.db import Database

log = logging.getLogger(__name__)

# Push services reject anything much larger; we send a tiny JSON object so this is
# only a guard against a pathological reminder title.
_MAX_BODY_CHARS = 300

# Per-subscription HTTP timeout. A wedged push service must not hold up the pass
# (or, in the web app, the /api/push/test request).
_SEND_TIMEOUT = 10


class PushError(RuntimeError):
    """Raised when push is asked for but cannot be done (missing deps or key)."""


# --- key handling ------------------------------------------------------------


def _b64(raw: bytes) -> str:
    """base64url without padding — the encoding every Web Push API expects."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def generate_private_key() -> str:
    """Return a fresh P-256 private key as the base64url raw scalar Web Push uses."""
    ec, _ = _crypto()
    key = ec.generate_private_key(ec.SECP256R1())
    return _b64(key.private_numbers().private_value.to_bytes(32, "big"))


def public_key_for(private_b64: str) -> str:
    """Derive the base64url `applicationServerKey` the browser must subscribe with."""
    ec, serialization = _crypto()
    value = int.from_bytes(_unb64(private_b64.strip()), "big")
    key = ec.derive_private_key(value, ec.SECP256R1())
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64(raw)


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on install
        raise PushError(
            "push needs the 'pywebpush' extra installed (pip install -e .)"
        ) from exc
    return ec, serialization


def public_key(cfg: Config) -> str:
    """The public key to hand the browser, or "" when push isn't usable.

    Returns empty rather than raising so the web app still starts and serves the
    board when push is off or misconfigured — the toggle in the UI just reports
    itself unavailable.
    """
    if not cfg.notify.enabled or not cfg.vapid_private_key:
        return ""
    try:
        return public_key_for(cfg.vapid_private_key)
    except Exception as exc:  # bad key material shouldn't take the app down
        log.error("cannot derive VAPID public key: %s", exc)
        return ""


# --- sending -----------------------------------------------------------------


def send_to_all(db: Database, cfg: Config, payload: dict, now: int) -> dict:
    """Deliver one payload to every stored subscription.

    Push services answer 404/410 for a subscription the browser has discarded
    (app uninstalled, data cleared). That is permanent, so we prune those rows
    immediately — otherwise dead endpoints accumulate forever and every pass pays
    to retry them. Everything else, including a push service we simply couldn't
    reach, is treated as transient and only bumps a counter.

    Returns a summary dict; one dead subscription never stops the others being
    tried, and no delivery failure raises.
    """
    if not cfg.notify.enabled:
        raise PushError("notifications are disabled in config ([notify] enabled)")
    if not cfg.vapid_private_key:
        raise PushError("NAGGY_VAPID_PRIVATE_KEY is not set")

    from pywebpush import WebPushException, webpush

    subs = db.list_subscriptions()
    sent = pruned = failed = 0
    data = json.dumps(payload)
    claims = {"sub": cfg.notify.subject}

    for sub in subs:
        try:
            webpush(
                subscription_info=sub.to_info(),
                data=data,
                vapid_private_key=cfg.vapid_private_key,
                # py_vapid mutates the claims dict (it stamps `exp`), so hand each
                # send its own copy.
                vapid_claims=dict(claims),
                ttl=cfg.notify.ttl_seconds,
                timeout=_SEND_TIMEOUT,
            )
            db.mark_subscription_ok(sub.id, now)
            sent += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                db.delete_subscription(sub.endpoint)
                pruned += 1
                log.info("pruned expired push subscription %s", sub.id)
            else:
                db.mark_subscription_failed(sub.id)
                failed += 1
                log.warning("push to subscription %s failed (%s): %s", sub.id, status, exc)
        except Exception as exc:
            # Transport-level trouble (DNS, TLS, timeout) arrives as a bare
            # requests error rather than a WebPushException. It says nothing about
            # this subscription's validity, so retry it next pass.
            db.mark_subscription_failed(sub.id)
            failed += 1
            log.warning("push to subscription %s errored: %s", sub.id, exc)

    return {"subscriptions": len(subs), "sent": sent, "pruned": pruned, "failed": failed}


# --- the notify pass ---------------------------------------------------------


def build_payload(
    reminders: list,
    now: int,
    tz: ZoneInfo,
    *,
    silent: bool = False,
    repost: bool = False,
    badge_count: int | None = None,
) -> dict:
    """Collapse the newly-pending reminders into one notification.

    One grouped notification per pass, not one per chore: a nagging app that fires
    five separate buzzes the moment a batch falls due gets its permission revoked.
    The `tag` makes a later pass replace the previous notification rather than
    stack on top of it.

    For a single chore the *timing* is the title and the chore is the body, not the
    other way round: Android gives the title one line and elides the rest, so a
    chore name long enough to matter is exactly the part that gets cut. "due today"
    always fits, and the body wraps.

    A `repost` — the same outstanding chores pushed again under
    `repeat_while_pending` — is always silent and never re-alerts. The first
    notification of a cycle gets your attention; after that it should just sit
    there, reappearing if you swipe it away rather than buzzing again.
    """
    if len(reminders) == 1:
        r = reminders[0]
        title = humanize_days(schedule.days_until(r.due_at, now, tz))
        body = r.title
        if r.notes:
            body = f"{body} · {r.notes}"
    else:
        title = f"{len(reminders)} chores need doing"
        body = ", ".join(r.title for r in reminders)
    return {
        "title": title,
        "body": body[:_MAX_BODY_CHARS],
        "url": "/",
        "tag": "naggy-pending",
        "silent": bool(silent or repost),
        "renotify": not repost,
        "badge_count": len(reminders) if badge_count is None else badge_count,
    }


def run_pass(
    db: Database, cfg: Config, now: int, tz: ZoneInfo, *, dry_run: bool = False
) -> dict:
    """Notify for every reminder that should be pushed about right now.

    Reminders are stamped with `notified_at` only when a delivery actually
    succeeded. If every send failed (server offline, push service hiccup) the
    stamp is left alone so the next pass retries instead of silently swallowing
    the nag. With no subscriptions at all we do nothing and stamp nothing, so a
    device that subscribes later still hears about what is outstanding.
    """
    active = db.list_active()
    due = schedule.due_for_notification(
        active, now,
        tz=tz,
        notify_at=cfg.notify.notify_at_hm(),
        repeat=cfg.notify.repeat_while_pending,
    )
    result = {"due": [r.title for r in due], "sent": 0, "notified": 0}
    if not due:
        return result

    # A pass where nothing is newly due is a repost of chores the user has already
    # been told about — it should slip in quietly rather than buzz again.
    repost = all(
        r.notified_at is not None and r.notified_at >= r.due_at for r in due
    )
    payload = build_payload(
        due, now, tz,
        silent=cfg.notify.silent,
        repost=repost,
        # Badge the true pending total, which under the default once-per-cycle
        # rule is more than the handful being pushed about in this pass.
        badge_count=len(schedule.board(active, now)["pending"]),
    )
    result["repost"] = repost

    if dry_run:
        result["payload"] = payload
        return result

    if not db.list_subscriptions():
        log.info("%d reminder(s) due but no push subscriptions registered", len(due))
        return result

    outcome = send_to_all(db, cfg, payload, now)
    result.update(outcome)
    if outcome["sent"]:
        for r in due:
            db.mark_notified(r.id, now)
        result["notified"] = len(due)
    return result
