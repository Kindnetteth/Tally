"""Turn a stream of 'what is playing' snapshots into watch history rows."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

from .db import Database, is_complete
from .matcher import Matcher
from .parser import parse
from .players import PlaybackState

log = logging.getLogger(__name__)

MIN_PLAY_SECONDS = 20       # ignore files opened for a few seconds
RESUME_WINDOW = 60 * 60     # reopening within an hour continues the same entry
GONE_AFTER = 15             # seconds without seeing a file before its session ends
MAX_TICK = 30               # cap time credited per tick (sleep, suspended laptop)


@dataclass
class Session:
    key: str
    state: PlaybackState
    first_seen: float
    last_seen: float
    watch_id: int | None = None
    played: float = 0.0          # seconds actually playing this session
    base_watched: float = 0.0    # watched_seconds already on a resumed row
    title_id: int | None = None
    season: int | None = None
    episode: int | None = None
    extra: dict = field(default_factory=dict)


class Tracker:
    def __init__(self, db: Database, matcher: Matcher):
        self.db = db
        self.matcher = matcher
        self.sessions: dict[str, Session] = {}

    def now_playing(self) -> list[dict]:
        out = []
        for s in self.sessions.values():
            if s.watch_id is None:
                continue
            out.append({"watch_id": s.watch_id, "player": s.state.player, "paused": s.state.paused,
                        "position": s.state.position, "duration": s.state.duration,
                        "exact": s.state.exact})
        return out

    def update(self, states: list[PlaybackState], now: float | None = None) -> None:
        now = time.time() if now is None else now
        seen = set()
        for state in states:
            key = os.path.normcase(state.path)
            seen.add(key)
            session = self.sessions.get(key)
            if session is None:
                session = Session(key=key, state=state, first_seen=now, last_seen=now)
                self.sessions[key] = session
            else:
                if state.paused is not True:
                    session.played += min(max(now - session.last_seen, 0), MAX_TICK)
                session.state = state
                session.last_seen = now
            self._sync(session, now)
        for key in list(self.sessions):
            if key not in seen and now - self.sessions[key].last_seen > GONE_AFTER:
                ended = self.sessions.pop(key)
                if ended.watch_id:
                    log.info("stopped %s", os.path.basename(ended.state.path))

    def _sync(self, s: Session, now: float) -> None:
        st = s.state
        if s.watch_id is None:
            if s.played < MIN_PLAY_SECONDS:
                return
            self._open_row(s, now)
        position = st.position
        duration = st.duration
        estimated = not st.exact
        watched = s.base_watched + s.played
        if not st.exact:
            duration = duration or self.db.runtime_seconds(s.title_id, s.season, s.episode)
            position = watched
            if duration:
                position = min(position, duration)
        row = self.db.one("SELECT max_position, completed, title_id, season, episode FROM watches WHERE id = ?",
                          (s.watch_id,))
        if row is None:  # deleted from the web page while playing
            s.watch_id = None
            s.played = 0
            return
        # The matcher may have remapped the episode since we opened the row
        s.title_id, s.season, s.episode = row["title_id"], row["season"], row["episode"]
        max_pos = max(row["max_position"] or 0, position or 0)
        completed = 1 if (row["completed"] or is_complete(max_pos, duration)) else 0
        self.db.execute(
            """UPDATE watches SET last_seen_at=?, position=?, duration=COALESCE(?, duration),
               max_position=?, watched_seconds=?, completed=?, estimated=?, player=? WHERE id=?""",
            (now, position, duration, max_pos, watched, completed, int(estimated), st.player, s.watch_id))

    def _open_row(self, s: Session, now: float) -> None:
        st = s.state
        recent = self.db.one(
            "SELECT id, watched_seconds, title_id, season, episode FROM watches "
            "WHERE path = ? AND last_seen_at > ? AND completed = 0 ORDER BY last_seen_at DESC LIMIT 1",
            (st.path, now - RESUME_WINDOW))
        if recent:
            s.watch_id = recent["id"]
            s.base_watched = recent["watched_seconds"] or 0
            s.title_id, s.season, s.episode = recent["title_id"], recent["season"], recent["episode"]
            log.info("resumed %s", os.path.basename(st.path))
            return
        parsed = parse(st.path)
        title_id = self.matcher.title_for(parsed)
        raw_episode = parsed.episode if parsed.loose else None
        started = now - s.played
        cur = self.db.execute(
            """INSERT INTO watches(path, filename, title_id, season, episode, episode_end, air_date, raw_episode,
               player, started_at, last_seen_at, position, duration, max_position, estimated)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (st.path, parsed.filename, title_id, parsed.season, parsed.episode, parsed.episode_end,
             parsed.air_date, raw_episode, st.player, started, now, st.position, st.duration,
             st.position, int(not st.exact)))
        s.watch_id = cur.lastrowid
        s.title_id, s.season, s.episode = title_id, parsed.season, parsed.episode
        # Absolute anime numbers can be mapped right away if we already know the show
        if raw_episode is not None or parsed.air_date:
            self.matcher.map_episodes(title_id)
        log.info("started %s -> %s S%sE%s", parsed.filename, parsed.title, parsed.season, parsed.episode)
