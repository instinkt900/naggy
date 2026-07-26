"""TOML configuration.

One file (`config.toml`; the tracked template is `config.example.toml`) holds all
runtime settings. We use nested dataclasses plus `_known()` so an unknown TOML key
fails loudly rather than being silently dropped — a typo'd setting should be a
startup error, not a mystery. Secrets come from the environment only
(`NAGGY_VAPID_PRIVATE_KEY` today, `NAGGY_HA_TOKEN` in future) and are rejected if
placed in the TOML — they aren't fields of any section, so `_known()` catches them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+ ships tomllib; fall back to the tomli backport otherwise.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


class ConfigError(RuntimeError):
    """Raised for any malformed or disallowed configuration."""


_HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


@dataclass
class DatabaseConfig:
    path: str = "naggy.db"


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    timezone: str = "UTC"


@dataclass
class NotifyConfig:
    enabled: bool = False
    # VAPID contact address, required by push services so they can reach the
    # operator about a misbehaving application server. "mailto:" or "https:".
    subject: str = ""
    # In-process poller cadence. 0 turns it off, for running `naggy notify` from
    # cron instead; the default suits the Docker deployment, where there is no
    # cron in the container.
    poll_seconds: int = 300
    # How long the push service should hold a message for a phone that's offline.
    ttl_seconds: int = 86_400
    # Local "HH:MM" to hold notifications until, or "" to push the moment a chore
    # falls due (which is whatever time of day it was last addressed).
    notify_at: str = ""
    # Never buzz or ring — the notification just appears in the shade.
    silent: bool = False
    # Re-push an outstanding chore on every pass, so swiping the notification away
    # only gets rid of it until the next one. The repost is always silent; only
    # the first notification of a cycle alerts. Cadence is `poll_seconds`.
    repeat_while_pending: bool = False
    # Show the pending count on the home-screen app icon (Badging API).
    badge: bool = True

    def notify_at_hm(self) -> tuple[int, int] | None:
        """`notify_at` as (hour, minute), or None when unset. Validated at load."""
        if not self.notify_at:
            return None
        hour, minute = self.notify_at.split(":")
        return int(hour), int(minute)


@dataclass
class Config:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    web: WebConfig = field(default_factory=WebConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    # Env-only, never from TOML. Reserved for the future Home Assistant feed;
    # empty today.
    ha_token: str = ""
    # Env-only: the VAPID signing key. Anyone holding it can push to every
    # subscribed device, so it must never land in a file that could be committed.
    vapid_private_key: str = ""


def _known(section: dict, cls) -> dict:
    """Keep only keys that map to a field on `cls`, rejecting anything else so a
    misspelled key surfaces immediately instead of being ignored."""
    allowed = {f.name for f in cls.__dataclass_fields__.values()}
    unknown = set(section) - allowed
    if unknown:
        raise ConfigError(f"unknown keys in [{cls.__name__.replace('Config', '').lower()}]: {sorted(unknown)}")
    return {k: v for k, v in section.items() if k in allowed}


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    with p.open("rb") as fh:
        raw = tomllib.load(fh)

    unknown_sections = set(raw) - {"database", "web", "notify"}
    if unknown_sections:
        raise ConfigError(f"unknown config sections: {sorted(unknown_sections)}")

    database = DatabaseConfig(**_known(raw.get("database", {}), DatabaseConfig))
    web = WebConfig(**_known(raw.get("web", {}), WebConfig))
    notify = NotifyConfig(**_known(raw.get("notify", {}), NotifyConfig))

    cfg = Config(database=database, web=web, notify=notify)
    cfg.ha_token = os.environ.get("NAGGY_HA_TOKEN", "").strip()
    cfg.vapid_private_key = os.environ.get("NAGGY_VAPID_PRIVATE_KEY", "").strip()

    if notify.enabled:
        # Fail at startup rather than at the first push: a subject the push
        # service rejects would otherwise only show up as a 403 hours later.
        if not notify.subject.startswith(("mailto:", "https://")):
            raise ConfigError(
                "[notify] subject must be a 'mailto:' or 'https://' contact URL "
                f"(got {notify.subject!r})"
            )
        if notify.poll_seconds < 0:
            raise ConfigError("[notify] poll_seconds must be >= 0")
        if notify.notify_at and not _HHMM.fullmatch(notify.notify_at):
            raise ConfigError(
                f'[notify] notify_at must be 24-hour "HH:MM" (got {notify.notify_at!r})'
            )

    return cfg
