"""Where Tally keeps its files on each platform."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Tally"
DEFAULT_PORT = 8737


def data_dir() -> Path:
    override = os.environ.get("TALLY_HOME")
    if override:
        path = Path(override).expanduser()
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    elif sys.platform.startswith("win"):
        path = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "tally"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "tally.db"


def log_path() -> Path:
    return data_dir() / "tally.log"


def mpv_ipc_path() -> str:
    """Socket (macOS/Linux) or named pipe (Windows) that mpv and IINA talk on."""
    if sys.platform.startswith("win"):
        return r"\\.\pipe\tally-mpv"
    return str(data_dir() / "mpv.sock")
