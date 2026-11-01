"""Bring in what you watched in IINA before Tally was running.

IINA keeps a playback history (history.plist, an NSKeyedArchiver file) with
each file's path, when it was opened and its length. Where you stopped is in
mpv's watch_later folder, one file per video named by the MD5 IINA stores.
mpv drops that file when you finish a video, so an entry without one is
counted as watched to the end (marked estimated).

IINA also remembers everything else you opened in it: music, phone clips,
screen recordings, your own edits, course videos, downloaded clips. Only
files that look like a released show or movie are brought in: an S01E02
style episode, or release tags (1080p, x265, WEBRip, BluRay...) in the file
or folder name, which is how downloaded shows and movies are named.
"""

from __future__ import annotations

import logging
import plistlib
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from . import prefs as user_prefs
from .db import Database, is_complete
from .parser import looks_like_release, parse

log = logging.getLogger(__name__)

NS_EPOCH = 978307200  # 2001-01-01, where NSDate counts from

@dataclass
class Entry:
    path: str
    opened_at: float
    duration: float | None
    position: float | None  # None when IINA kept no resume point


def iina_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "com.colliderli.iina"


def available() -> bool:
    return sys.platform == "darwin" and (iina_dir() / "history.plist").is_file()


def read_history(base: Path | None = None) -> list[Entry]:
    base = base or iina_dir()
    archive = plistlib.loads((base / "history.plist").read_bytes())
    objects = archive["$objects"]

    def deref(v):
        while isinstance(v, plistlib.UID):
            v = objects[v.data]
        return v

    entries = []
    for ref in deref(archive["$top"]["root"]).get("NS.objects", []):
        item = deref(ref)
        try:
            url = deref(deref(item["IINAPHUrl"])["NS.relative"])
            if not url.startswith("file://"):
                continue
            path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
            opened = deref(deref(item["IINAPHDate"])["NS.time"]) + NS_EPOCH
            duration = deref(item.get("IINAPHDuration")) or None
            md5 = str(deref(item.get("IINAPHMpvmd5")) or "").upper()
        except (KeyError, TypeError, AttributeError):
            continue
        position = None
        resume = base / "watch_later" / md5
        if md5 and resume.is_file():
            m = re.search(r"^start=([\d.]+)", resume.read_text(encoding="utf-8", errors="replace"), re.M)
            position = float(m.group(1)) if m else None
        entries.append(Entry(path, opened, duration, position))
    return entries


def import_history(db: Database, matcher, base: Path | None = None) -> int:
    """Add IINA's history to Tally. Files Tally already logged are skipped, so it's safe to run again."""
    known = {row["path"] for row in db.query("SELECT DISTINCT path FROM watches")}
    settings = user_prefs.load(db)
    added = 0
    titles = set()
    new_ids: set[int] = set()
    for e in sorted(read_history(base), key=lambda e: e.opened_at):
        if e.path in known or not looks_like_release(e.path) or user_prefs.ignored(e.path, settings):
            continue
        if e.position is None:
            position, estimated = e.duration, True
        else:
            position, estimated = e.position, False
        if position is not None and position < settings["min_play_seconds"]:
            continue  # opened and closed again right away
        parsed = parse(e.path)
        title_id = matcher.title_for(parsed)
        raw_episode = parsed.episode if parsed.loose else None
        completed = estimated or is_complete(position, e.duration, *user_prefs.finish_rule(settings))
        cur = db.execute(
            """INSERT INTO watches(path, filename, title_id, season, episode, episode_end, air_date, raw_episode,
               player, started_at, last_seen_at, position, duration, max_position, watched_seconds,
               completed, estimated)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (e.path, parsed.filename, title_id, parsed.season, parsed.episode, parsed.episode_end,
             parsed.air_date, raw_episode, "IINA", e.opened_at, e.opened_at + (position or 0), position,
             e.duration, position, position or 0, int(completed), int(estimated)))
        known.add(e.path)
        titles.add(title_id)
        new_ids.add(cur.lastrowid)
        added += 1
    for title_id in titles:
        matcher.map_episodes(title_id)
        finish_moved_on(db, title_id, new_ids)
    log.info("imported %d entries from IINA's history", added)
    return added


def finish_moved_on(db: Database, title_id: int, ids: set[int]) -> int:
    """Count an episode as finished if you went on to a later one of the same show afterwards.

    Reopening a file in IINA overwrites its resume point, so an episode you
    finished can look like you stopped seconds in. Only rows in ids are changed.
    """
    rows = db.query("SELECT id, season, episode, started_at, completed FROM watches "
                    "WHERE title_id = ? AND season IS NOT NULL AND episode IS NOT NULL", (title_id,))
    changed = 0
    for r in rows:
        if r["completed"] or r["id"] not in ids:
            continue
        if any((o["season"], o["episode"]) > (r["season"], r["episode"]) and o["started_at"] > r["started_at"]
               for o in rows):
            db.execute("UPDATE watches SET completed = 1, estimated = 1 WHERE id = ?", (r["id"],))
            changed += 1
    return changed
