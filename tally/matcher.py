"""Background worker that matches titles to the metadata databases."""

from __future__ import annotations

import logging
import queue
import threading
import time
import urllib.error

from . import metadata
from .db import Database
from .parser import ParsedName

log = logging.getLogger(__name__)

RETRY_AFTER = 12 * 3600  # seconds before retrying a title that did not match


class Matcher:
    def __init__(self, db: Database):
        self.db = db
        self.jobs: queue.Queue[int] = queue.Queue()
        self._pending: set[int] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ---- called from the tracker ---------------------------------------

    def title_for(self, parsed: ParsedName) -> int:
        """Title id for a parsed name, creating a placeholder and queueing a lookup if new."""
        title_id = self.db.title_for_alias(parsed.key)
        if title_id is None:
            title_id = self.db.create_placeholder(parsed.key, parsed.kind, parsed.title, parsed.year)
            self.enqueue(title_id)
        return title_id

    def enqueue(self, title_id: int) -> None:
        with self._lock:
            if title_id in self._pending:
                return
            self._pending.add(title_id)
        self.jobs.put(title_id)

    # ---- worker ---------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="tally-matcher", daemon=True)
        self._thread.start()
        cutoff = time.time() - RETRY_AFTER
        for row in self.db.query("SELECT id FROM titles WHERE matched = 0 AND (lookup_at IS NULL OR lookup_at < ?)",
                                 (cutoff,)):
            self.enqueue(row["id"])

    def _run(self) -> None:
        while True:
            title_id = self.jobs.get()
            try:
                self.lookup(title_id)
            except Exception:  # never let one bad title stop the worker
                log.exception("lookup failed for title %s", title_id)
            finally:
                with self._lock:
                    self._pending.discard(title_id)

    def lookup(self, title_id: int) -> None:
        row = self.db.one("SELECT * FROM titles WHERE id = ?", (title_id,))
        if row is None or row["locked"]:
            return
        alias = self.db.one("SELECT query, year FROM aliases WHERE title_id = ? ORDER BY rowid LIMIT 1", (title_id,))
        query = alias["query"] if alias else row["name"]
        year = alias["year"] if alias else row["year"]
        hint = self.db.one(
            "SELECT CASE WHEN raw_episode IS NULL THEN season END AS season, "
            "COALESCE(raw_episode, episode) AS ep, filename FROM watches "
            "WHERE title_id = ? ORDER BY started_at DESC LIMIT 1", (title_id,))
        season = hint["season"] if hint else None
        episode = hint["ep"] if hint else None
        anime = bool(hint and hint["filename"].lstrip().startswith("["))
        tmdb_key = self.db.get_setting("tmdb_key")
        log.info("looking up %s %r (%s)", row["kind"], query, year)
        try:
            cand = metadata.best_match(row["kind"], query, year, tmdb_key, season=season,
                                       episode=episode, anime=anime)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            log.info("lookup for %r failed, will retry later: %s", query, exc)
            self.db.execute("UPDATE titles SET lookup_at = ? WHERE id = ?", (time.time() - RETRY_AFTER + 600, title_id))
            return
        if cand is None:
            log.info("no match for %r", query)
            self.db.execute("UPDATE titles SET lookup_at = ? WHERE id = ?", (time.time(), title_id))
            return
        self.apply(title_id, cand)

    def apply(self, title_id: int, cand: metadata.Candidate, locked: bool = False) -> int:
        """Store a match on title_id (merging into an existing title if we already have it)."""
        poster = metadata.download_image(cand.poster_url) if cand.poster_url else None
        with self.db.write_lock:
            existing = self.db.one("SELECT id FROM titles WHERE source = ? AND source_id = ?",
                                   (cand.source, cand.source_id))
            if existing and existing["id"] != title_id:
                self.db.merge_titles(existing["id"], title_id)
                title_id = existing["id"]
            conn = self.db.connect()
            conn.execute(
                """UPDATE titles SET kind=?, name=?, year=?, overview=?, runtime=?, source=?, source_id=?,
                   imdb_id=?, status=?, matched=1, locked=MAX(locked, ?), lookup_at=? WHERE id=?""",
                (cand.kind, cand.name, cand.year, cand.overview, cand.runtime, cand.source, cand.source_id,
                 cand.imdb_id, cand.status, int(locked), time.time(), title_id))
            if poster:
                conn.execute("UPDATE titles SET poster=?, poster_type=? WHERE id=?", (poster[0], poster[1], title_id))
            if cand.episodes:
                conn.execute("DELETE FROM episodes WHERE title_id = ?", (title_id,))
                conn.executemany(
                    "INSERT OR REPLACE INTO episodes(title_id, season, number, name, airdate, runtime) VALUES(?,?,?,?,?,?)",
                    [(title_id, *e) for e in cand.episodes])
            conn.commit()
        self.map_episodes(title_id)
        log.info("matched title %s -> %s (%s:%s)", title_id, cand.name, cand.source, cand.source_id)
        return title_id

    def map_episodes(self, title_id: int) -> None:
        """Fill in season/episode for files that only had an absolute or dated episode number."""
        eps = self.db.query("SELECT season, number, airdate FROM episodes WHERE title_id = ? AND season > 0 "
                            "ORDER BY season, number", (title_id,))
        if not eps:
            return
        by_date = {e["airdate"]: (e["season"], e["number"]) for e in eps if e["airdate"]}
        season_sizes: dict[int, int] = {}
        for e in eps:
            season_sizes[e["season"]] = max(season_sizes.get(e["season"], 0), e["number"])
        # raw_episode is only set when the file name gave no season next to the episode
        watches = self.db.query(
            "SELECT id, season, episode, raw_episode, air_date FROM watches WHERE title_id = ? "
            "AND (raw_episode IS NOT NULL OR air_date IS NOT NULL)", (title_id,))
        for w in watches:
            raw = w["raw_episode"]
            new = None
            if w["air_date"] and w["air_date"] in by_date:
                new = by_date[w["air_date"]]
            elif raw is None:
                pass
            elif w["season"] is None:
                if raw <= season_sizes.get(1, 0):
                    new = (1, raw)
                elif raw <= len(eps):
                    new = (eps[raw - 1]["season"], eps[raw - 1]["number"])
            elif raw > season_sizes.get(w["season"], raw) and w["episode"] == raw:
                # "Season 2" folder but absolute numbering
                if raw <= len(eps):
                    new = (eps[raw - 1]["season"], eps[raw - 1]["number"])
            if new and (new[0], new[1]) != (w["season"], w["episode"]):
                self.db.execute("UPDATE watches SET season = ?, episode = ? WHERE id = ?",
                                (new[0], new[1], w["id"]))
