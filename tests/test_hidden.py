import datetime as dt
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from tally.api import Api
from tally.db import Database

DAY = 86400


class HiddenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "t.db")
        self.api = Api(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def title(self, kind, name, hidden=0):
        return self.db.execute(
            "INSERT INTO titles(kind, name, matched, hidden, created_at) VALUES(?, ?, 1, ?, 0)",
            (kind, name, hidden)).lastrowid

    def watch(self, tid, season=None, episode=None, completed=1, at=None):
        at = time.time() - DAY if at is None else at
        return self.db.execute(
            "INSERT INTO watches(path, filename, title_id, season, episode, started_at, last_seen_at, "
            "watched_seconds, completed) VALUES(?, 'x.mkv', ?, ?, ?, ?, ?, 300, ?)",
            (str(self.root / "x.mkv"), tid, season, episode, at, at, completed)).lastrowid

    def test_hidden_title_is_gone_from_screens_but_kept(self):
        tid = self.title("show", "Secret Show", hidden=1)
        self.db.execute("INSERT INTO episodes(title_id, season, number, airdate) VALUES(?, 1, 1, ?)",
                        (tid, dt.date.today().isoformat()))
        self.db.execute("INSERT INTO episodes(title_id, season, number, airdate) VALUES(?, 1, 2, ?)",
                        (tid, dt.date.today().isoformat()))
        wid = self.watch(tid, 1, 1, completed=1, at=time.time() - 10 * DAY)

        home = self.api.home([{"watch_id": wid, "paused": False, "exact": True}], week_start=time.time() - 7 * DAY)
        self.assertEqual(home["now_playing"], [])
        self.assertEqual(home["continue"], [])
        self.assertEqual(home["week"], [])
        self.assertEqual(home["stats"]["week_seconds"], 0)
        self.assertEqual(home["stats"]["total_seconds"], 0)
        self.assertEqual(self.api.updates(), [])
        self.assertEqual(self.api.history(), [])

        # The show page itself still works, so hiding can be undone
        data = self.api.title(tid)
        self.assertTrue(data["title"]["hidden"])
        self.assertEqual(len(data["history"]), 1)

        self.assertEqual([t["id"] for t in self.api.hidden_titles()], [tid])
        self.api.set_hidden(tid, False)
        self.assertEqual([w["title_id"] for w in self.api.history()], [tid])
        self.assertEqual(self.api.hidden_titles(), [])

    def test_shows_and_movies_keep_the_hidden_flag_for_the_page(self):
        show = self.title("show", "Hidden Show", hidden=1)
        movie = self.title("movie", "Hidden Movie", hidden=1)
        self.watch(show, 1, 1)
        self.watch(movie)

        self.assertEqual([s["title"]["hidden"] for s in self.api.shows()], [True])
        self.assertEqual([m["title"]["hidden"] for m in self.api.movies()], [True])
        self.assertEqual({t["id"] for t in self.api.hidden_titles()}, {show, movie})


class MigrationTests(unittest.TestCase):
    def test_old_database_gains_the_hidden_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE titles (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, "
                         "year INTEGER, overview TEXT, runtime INTEGER, poster BLOB, poster_type TEXT, "
                         "source TEXT, source_id TEXT, imdb_id TEXT, status TEXT, matched INTEGER NOT NULL DEFAULT 0, "
                         "locked INTEGER NOT NULL DEFAULT 0, lookup_at REAL, created_at REAL NOT NULL)")
            conn.execute("INSERT INTO titles(kind, name, created_at) VALUES('show', 'Old', 0)")
            conn.commit()
            conn.close()

            db = Database(path)
            self.assertEqual(db.one("SELECT hidden FROM titles")["hidden"], 0)
            db.execute("UPDATE titles SET hidden = 1")
            self.assertEqual(db.one("SELECT hidden FROM titles")["hidden"], 1)


if __name__ == "__main__":
    unittest.main()
