"""Your settings: how Tally looks and what it logs.

Each one is stored in the settings table as JSON under "pref:<name>".
"""

from __future__ import annotations

import json
import os

from .db import Database

ACCENTS = ("orchid", "violet", "ocean", "mint", "sunset", "graphite")
THEMES = ("system", "light", "dark", "midnight", "glow")
ICONS = ("match", *ACCENTS, "midnight")
# Finished at this percentage, or once this many seconds are left (end credits)
FINISH_RULES = {80: 300, 85: 240, 90: 120, 95: 60, 98: 0}

DEFAULTS = {
    "theme": "system",
    "accent": "orchid",
    "app_icon": "match",
    "paused": False,
    "only_releases": True,
    "ignored_folders": [],
    "min_play_seconds": 20,
    "finish_at": 85,
    "week_start": "monday",
    "hide_spoilers": False,
}

_CHOICES = {
    "theme": THEMES,
    "accent": ACCENTS,
    "app_icon": ICONS,
    "min_play_seconds": (20, 60, 120, 300),
    "finish_at": tuple(FINISH_RULES),
    "week_start": ("monday", "sunday"),
}


def load(db: Database) -> dict:
    prefs = dict(DEFAULTS)
    for row in db.query("SELECT key, value FROM settings WHERE key LIKE 'pref:%'"):
        name = row["key"][5:]
        if name in prefs:
            try:
                prefs[name] = json.loads(row["value"])
            except (TypeError, ValueError):
                pass
    return prefs


def save(db: Database, changes: dict) -> dict:
    """Validate and store changed settings. Unknown names are ignored; bad values raise ValueError."""
    for name, value in changes.items():
        if name not in DEFAULTS:
            continue
        if name in _CHOICES:
            if value not in _CHOICES[name]:
                raise ValueError(f"{name} can't be {value!r}")
        elif name == "ignored_folders":
            if not isinstance(value, list):
                raise ValueError("ignored_folders must be a list")
            value = [str(v).strip() for v in value if str(v).strip()]
        else:
            value = bool(value)
        db.set_setting(f"pref:{name}", json.dumps(value))
    return load(db)


def finish_rule(prefs: dict) -> tuple[float, float]:
    """(ratio, seconds left) at which a video counts as finished."""
    pct = prefs.get("finish_at", DEFAULTS["finish_at"])
    return pct / 100, FINISH_RULES.get(pct, 240)


def icon_name(prefs: dict) -> str:
    return prefs["accent"] if prefs.get("app_icon", "match") == "match" else prefs["app_icon"]


def ignored(path: str, prefs: dict) -> bool:
    target = os.path.normcase(os.path.abspath(path))
    for folder in prefs.get("ignored_folders") or []:
        folder = os.path.normcase(os.path.abspath(os.path.expanduser(folder))).rstrip("\\/")
        if folder and (target == folder or target.startswith(folder + os.sep)):
            return True
    return False


def should_log(path: str, prefs: dict) -> bool:
    from .parser import looks_like_release

    if ignored(path, prefs):
        return False
    return looks_like_release(path) if prefs.get("only_releases", True) else True
