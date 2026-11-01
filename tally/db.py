"""SQLite storage. Everything (history, metadata, posters, settings) lives in one file."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS titles (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,              -- 'show' or 'movie'
    name          TEXT NOT NULL,
    year          INTEGER,
    overview      TEXT,
    runtime       INTEGER,                    -- minutes
    poster        BLOB,
    poster_type   TEXT,
    source        TEXT,                       -- 'tvmaze', 'tmdb', 'imdb' or NULL when unmatched
    source_id     TEXT,
    imdb_id       TEXT,
    status        TEXT,                       -- show status from the database, e.g. 'Ended'
    matched       INTEGER NOT NULL DEFAULT 0,
    locked        INTEGER NOT NULL DEFAULT 0, -- user picked the match by hand
    lookup_at     REAL,
    created_at    REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS titles_source ON titles(source, source_id) WHERE source IS NOT NULL;

-- Parsed names that point at a title, so each show is looked up once.
CREATE TABLE IF NOT EXISTS aliases (
    key       TEXT PRIMARY KEY,
    title_id  INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    query     TEXT NOT NULL,
    year      INTEGER
);

CREATE TABLE IF NOT EXISTS episodes (
    title_id  INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    season    INTEGER NOT NULL,
    number    INTEGER NOT NULL,
    name      TEXT,
    airdate   TEXT,
    runtime   INTEGER,
    PRIMARY KEY (title_id, season, number)
);

CREATE TABLE IF NOT EXISTS watches (
    id              INTEGER PRIMARY KEY,
    path            TEXT NOT NULL,
    filename        TEXT NOT NULL,
    title_id        INTEGER REFERENCES titles(id) ON DELETE SET NULL,
    season          INTEGER,
    episode         INTEGER,
    episode_end     INTEGER,
    air_date        TEXT,
    raw_episode     INTEGER,                  -- episode number as written in the file name
    player          TEXT,
    started_at      REAL NOT NULL,
    last_seen_at    REAL NOT NULL,
    position        REAL,                     -- seconds
    duration        REAL,                     -- seconds
    max_position    REAL,
    watched_seconds REAL NOT NULL DEFAULT 0,
    completed       INTEGER NOT NULL DEFAULT 0,
    estimated       INTEGER NOT NULL DEFAULT 0, -- position was guessed from time open, not read from the player
    manual          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS watches_started ON watches(started_at);
CREATE INDEX IF NOT EXISTS watches_title ON watches(title_id, season, episode);
CREATE INDEX IF NOT EXISTS watches_path ON watches(path);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

COMPLETE_RATIO = 0.90
COMPLETE_REMAINING_SECONDS = 120


def is_complete(position: float | None, duration: float | None) -> bool:
    if not position or not duration or duration <= 0:
        return False
    if position / duration >= COMPLETE_RATIO:
        return True
    # Long videos with credits: within the last two minutes counts as finished
    return duration > 600 and duration - position <= COMPLETE_REMAINING_SECONDS


class Database:
    """Thin wrapper that hands each thread its own connection."""

    def __init__(self, path: Path | str | None = None):
        self.path = str(path or paths.db_path())
        self._local = threading.local()
        self.write_lock = threading.RLock()
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def query(self, sql: str, args=()) -> list[sqlite3.Row]:
        return self.connect().execute(sql, args).fetchall()

    def one(self, sql: str, args=()) -> sqlite3.Row | None:
        return self.connect().execute(sql, args).fetchone()

    def execute(self, sql: str, args=()) -> sqlite3.Cursor:
        with self.write_lock:
            conn = self.connect()
            cur = conn.execute(sql, args)
            conn.commit()
            return cur

    # ---- settings -------------------------------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row and row["value"] is not None else default

    def set_setting(self, key: str, value: str | None) -> None:
        self.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    # ---- titles ---------------------------------------------------------

    def title_for_alias(self, key: str) -> int | None:
        row = self.one("SELECT title_id FROM aliases WHERE key = ?", (key,))
        return row["title_id"] if row else None

    def create_placeholder(self, key: str, kind: str, name: str, year: int | None) -> int:
        with self.write_lock:
            existing = self.title_for_alias(key)
            if existing:
                return existing
            conn = self.connect()
            cur = conn.execute("INSERT INTO titles(kind, name, year, created_at) VALUES(?, ?, ?, ?)",
                               (kind, name, year, time.time()))
            title_id = cur.lastrowid
            conn.execute("INSERT INTO aliases(key, title_id, query, year) VALUES(?, ?, ?, ?)",
                         (key, title_id, name, year))
            conn.commit()
            return title_id

    def merge_titles(self, keep_id: int, drop_id: int) -> None:
        """Point everything at keep_id and delete drop_id."""
        if keep_id == drop_id:
            return
        with self.write_lock:
            conn = self.connect()
            conn.execute("UPDATE watches SET title_id = ? WHERE title_id = ?", (keep_id, drop_id))
            conn.execute("UPDATE aliases SET title_id = ? WHERE title_id = ?", (keep_id, drop_id))
            conn.execute("DELETE FROM titles WHERE id = ?", (drop_id,))
            conn.commit()

    def runtime_seconds(self, title_id: int | None, season: int | None, episode: int | None) -> float | None:
        if not title_id:
            return None
        if season is not None and episode is not None:
            row = self.one("SELECT runtime FROM episodes WHERE title_id=? AND season=? AND number=?",
                           (title_id, season, episode))
            if row and row["runtime"]:
                return row["runtime"] * 60.0
        row = self.one("SELECT runtime FROM titles WHERE id = ?", (title_id,))
        return row["runtime"] * 60.0 if row and row["runtime"] else None
