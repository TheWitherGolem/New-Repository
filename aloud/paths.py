"""Filesystem locations used by the app.

Everything the app writes lives under the user's profile so that Aloud can be
run from anywhere (including a read-only folder) without needing admin rights.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import APP_NAME


def _base(env_var: str, fallback: Path) -> Path:
    raw = os.environ.get(env_var)
    return Path(raw) if raw else fallback


def config_dir() -> Path:
    """Where settings and presets live (roams with the user profile)."""
    return _base("APPDATA", Path.home() / ".config") / APP_NAME


def data_dir() -> Path:
    """Where bulky, re-downloadable data lives (voice models, logs)."""
    return _base("LOCALAPPDATA", Path.home() / ".local" / "share") / APP_NAME


def voices_dir() -> Path:
    return data_dir() / "voices"


def config_file() -> Path:
    return config_dir() / "config.json"


def log_file() -> Path:
    return data_dir() / "aloud.log"


def ensure_dirs() -> None:
    for path in (config_dir(), data_dir(), voices_dir()):
        path.mkdir(parents=True, exist_ok=True)
