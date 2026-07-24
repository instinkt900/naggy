"""TOML configuration.

One file (`config.toml`; the tracked template is `config.example.toml`) holds all
runtime settings. We use nested dataclasses plus `_known()` so an unknown TOML key
fails loudly rather than being silently dropped — a typo'd setting should be a
startup error, not a mystery. Secrets (there are none required today, but a future
Home Assistant token would be one) come from the environment only and are actively
rejected if placed in the TOML.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+ ships tomllib; fall back to the tomli backport otherwise.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


class ConfigError(RuntimeError):
    """Raised for any malformed or disallowed configuration."""


@dataclass
class DatabaseConfig:
    path: str = "naggy.db"


@dataclass
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    timezone: str = "UTC"


@dataclass
class Config:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    web: WebConfig = field(default_factory=WebConfig)
    # Env-only, never from TOML. Reserved for the future Home Assistant feed;
    # empty today.
    ha_token: str = ""


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

    unknown_sections = set(raw) - {"database", "web"}
    if unknown_sections:
        raise ConfigError(f"unknown config sections: {sorted(unknown_sections)}")

    database = DatabaseConfig(**_known(raw.get("database", {}), DatabaseConfig))
    web = WebConfig(**_known(raw.get("web", {}), WebConfig))

    cfg = Config(database=database, web=web)
    cfg.ha_token = os.environ.get("NAGGY_HA_TOKEN", "").strip()
    return cfg
