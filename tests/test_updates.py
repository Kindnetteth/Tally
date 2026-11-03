import datetime as dt
import tempfile
import time
import unittest
from pathlib import Path

from tally.api import Api
from tally.db import Database

DAY = 86400


def day(offset: int) -> str:
    return (dt.date.today() + dt.timedelta(days=offset)).isoformat()


class UpdatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.api = Api(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def show(self, name, seasons):
        """seasons: {season: [airdate offsets in days, or None for no date yet]}"""
        tid = self.db.execute("INSERT INTO titles(kind, name, matched, source, source_id, created_at) "
                              "VALUES('show', ?, 1, 'tvmaze', ?, 0)", (name, name)).lastrowid
        for season, offsets in seasons.items():
            for n, off in enumerate(offsets, 1):
                self.db.execute("INSERT INTO episodes(title_id, season, number, airdate) VALUES(?,?,?,?)",
                                (tid, season, n, day(off) if off is not None else None))
        return tid

    def watched(self, tid, season, episodes, days_ago):
        ts = time.time() - days_ago * DAY
        for e in episodes:
            self.db.execute("INSERT INTO watches(path, filename, title_id, season, episode, started_at, last_seen_at, "
                            "completed) VALUES('/x', 'x', ?, ?, ?, ?, ?, 1)", (tid, season, e, ts, ts))

    def kinds(self):
        return {i["title"]["name"]: (i["kind"], i["season"], i["episode"]) for i in self.api.updates()}

    def test_cases(self):
        a = self.show("New Season Out", {1: [-400, -393], 2: [-20, -13, -6]})
        self.watched(a, 1, [1, 2], 300)
        b = self.show("New Episodes", {1: [-30, -23, -16, -9, -2]})
        self.watched(b, 1, [1, 2, 3], 12)
        c = self.show("Premiere Soon", {1: [-100, -93], 2: [20, 27]})
        self.watched(c, 1, [1], 90)  # behind, but a premiere still shows
        d = self.show("Announced", {1: [-60, -53], 2: [None]})
        self.watched(d, 1, [1, 2], 40)
        e = self.show("Backlog", {1: [-400, -393, -386, -379]})
        self.watched(e, 1, [1], 10)  # the rest aired long before you watched: not news
        f = self.show("Next Episode Friday", {1: [-14, -7, 3]})
        self.watched(f, 1, [1, 2], 2)
        self.assertEqual(self.kinds(), {
            "New Season Out": ("new_season", 2, 1),
            "New Episodes": ("new_episodes", 1, 4),
            "Premiere Soon": ("premiere", 2, 1),
            "Announced": ("announced", 2, 1),
            "Next Episode Friday": ("next_episode", 1, 3),
        })


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.api = Api(self.db)
        self.tid = self.db.execute("INSERT INTO titles(kind, name, matched, created_at) VALUES('show', 'S', 1, 0)").lastrowid
        for n in (1, 2):
            self.db.execute("INSERT INTO episodes(title_id, season, number) VALUES(?, 1, ?)", (self.tid, n))

    def tearDown(self):
        self.tmp.cleanup()

    def watch(self, at, completed, manual=0):
        self.db.execute("INSERT INTO watches(path, filename, title_id, season, episode, started_at, last_seen_at, "
                        "completed, manual) VALUES('/x', 'x', ?, 1, 1, ?, ?, ?, ?)",
                        (self.tid, at, at, completed, manual))

    def test_rewatch_partway_offers_resume(self):
        self.watch(100, 1)  # watched it before
        self.watch(200, 0)  # rewatching, stopped partway
        self.assertEqual(self.api._show_state(self.tid)["state"], "resume")

    def test_marked_watched_after_stopping_moves_on(self):
        self.watch(100, 0)
        self.watch(200, 1, manual=1)
        self.assertEqual(self.api._show_state(self.tid)["state"], "next")


if __name__ == "__main__":
    unittest.main()
