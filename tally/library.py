"""Find episodes and movies on disk, so the page can offer a Play button.

Downloads usually sit next to each other: the next episode in the same
folder as the last one, the next season in a "Season 02" folder beside
"Season 01", or a new season pack in the same Downloads folder as the old
one. So the search starts from the folders you watched the show from and
looks in each, its parent, and the parent's other folders, two levels deep.
Files are recognised by parsing their names like the logger does.

Searches only happen when a page asks, and results are cached briefly, so
this costs nothing while Tally sits in the background.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .db import Database
from .parser import is_video_path, normalize, parse

CACHE_SECONDS = 120
MAX_ENTRIES = 4000  # stop a search after looking at this many files and folders
SKIP_DIRS = {"node_modules", "Library", "Applications", "System", ".Trash"}

_cache: dict[tuple, tuple[float, str | None]] = {}
_lock = threading.Lock()
_offered: set[str] = set()  # files this module found; only these can be played


def find_episode(db: Database, title_id: int, season: int | None, episode: int | None) -> str | None:
    """Path of a downloaded copy of this episode (or of the movie when episode is None), if there is one."""
    key = (title_id, season, episode)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS and (hit[1] is None or os.path.exists(hit[1])):
            return hit[1]
    found = _search(db, title_id, season, episode)
    with _lock:
        _cache[key] = (now, found)
        if found:
            _offered.add(found)
    return found


def existing(path: str | None) -> str | None:
    """path, if that file is still on disk (and remember it as playable)."""
    if path and os.path.isfile(path):
        with _lock:
            _offered.add(path)
        return path
    return None


def _search(db: Database, title_id: int, season: int | None, episode: int | None) -> str | None:
    title = db.one("SELECT name, kind FROM titles WHERE id = ?", (title_id,))
    if title is None:
        return None
    keys = {r["key"] for r in db.query("SELECT key FROM aliases WHERE title_id = ?", (title_id,))}
    names = {normalize(title["name"])} | {k.split(":")[1] for k in keys if k.count(":") >= 2}
    eps = [(r["season"], r["number"]) for r in db.query(
        "SELECT season, number FROM episodes WHERE title_id = ? AND season > 0 ORDER BY season, number", (title_id,))]

    def matches(path: str) -> bool:
        p = parse(path)
        if p.key not in keys and normalize(p.title) not in names:
            return False
        if episode is None:
            return p.kind == "movie" or p.episode is None
        return _episode_of(p, eps) == (season, episode)

    for path in _candidates(db, title_id):
        if matches(path):
            return path
    return None


def _episode_of(p, eps: list[tuple[int, int]]) -> tuple[int | None, int | None]:
    """(season, episode) for a parsed name, turning absolute anime numbering into seasons."""
    if p.episode is None:
        return p.season, None
    sizes: dict[int, int] = {}
    for s, n in eps:
        sizes[s] = max(sizes.get(s, 0), n)
    raw = p.episode
    if p.season is None:
        if not eps or raw <= sizes.get(1, 0):
            return 1, raw
        return eps[raw - 1] if raw <= len(eps) else (None, raw)
    if p.loose and raw > sizes.get(p.season, raw) and raw <= len(eps):
        return eps[raw - 1]  # "Season 2" folder but numbered from the start of the show
    return p.season, raw


def _candidates(db: Database, title_id: int):
    """Video files near where you watched this title, closest first."""
    dirs: list[Path] = []
    for row in db.query("SELECT path FROM watches WHERE title_id = ? ORDER BY last_seen_at DESC LIMIT 40",
                        (title_id,)):
        d = Path(row["path"]).parent
        if d not in dirs:
            dirs.append(d)
    seen: set[Path] = set()
    budget = [MAX_ENTRIES]
    # 1. the folders themselves; 2. their parents, two levels down (sibling season folders and packs)
    for d in dirs:
        yield from _scan(d, 0, seen, budget)
    for d in dirs:
        for parent in (d.parent, d.parent.parent):
            # never your whole home folder (or anything above it), or every mounted drive
            if Path.home().is_relative_to(parent) or str(parent) in ("/Volumes", "/media", "/mnt"):
                continue
            yield from _scan(parent, 2, seen, budget)


def _scan(folder: Path, depth: int, seen: set[Path], budget: list[int]):
    if folder in seen or budget[0] <= 0:
        return
    seen.add(folder)
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name)
    except OSError:
        return
    subdirs = []
    for entry in entries:
        budget[0] -= 1
        if budget[0] <= 0:
            return
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_file() and is_video_path(entry.name):
                yield entry.path
            elif depth > 0 and entry.is_dir() and entry.name not in SKIP_DIRS:
                subdirs.append(Path(entry.path))
        except OSError:
            continue
    for sub in subdirs:
        yield from _scan(sub, depth - 1, seen, budget)


# ---- playing ------------------------------------------------------------------

def play(path: str, player: str = "iina") -> None:
    """Open a file the page was offered, in IINA (or the system's default player)."""
    with _lock:
        allowed = path in _offered
    if not allowed or not os.path.isfile(path) or not is_video_path(path):
        raise ValueError("That file isn't available to play")
    if sys.platform == "darwin":
        if player == "iina" and _iina_installed():
            subprocess.run(["open", "-a", "IINA", path], check=True, timeout=15)
        else:
            subprocess.run(["open", path], check=True, timeout=15)
    elif sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", path])


def _iina_installed() -> bool:
    return any(Path(p).exists() for p in ("/Applications/IINA.app", str(Path.home() / "Applications/IINA.app")))
