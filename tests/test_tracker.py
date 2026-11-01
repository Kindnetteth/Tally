import tempfile
import unittest
from pathlib import Path

from tally.db import Database
from tally.matcher import Matcher
from tally.metadata import Candidate
from tally.players import PlaybackState, poll_all, Source
from tally.tracker import Tracker

PATH = "/Videos/Severance/Severance.S02E05.1080p.mkv"


class FakeMatcher(Matcher):
    """Matcher that never touches the network."""

    def enqueue(self, title_id):
        self.queued = getattr(self, "queued", []) + [title_id]


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.matcher = FakeMatcher(self.db)
        self.tracker = Tracker(self.db, self.matcher)

    def tearDown(self):
        self.tmp.cleanup()

    def play(self, t, position, duration=3000.0, paused=False, path=PATH, exact=True):
        state = PlaybackState(player="mpv", path=path, position=position, duration=duration,
                              paused=paused, exact=exact)
        self.tracker.update([state], now=t)

    def watches(self):
        return self.db.query("SELECT * FROM watches ORDER BY id")

    def test_short_peek_is_ignored(self):
        self.play(0, 0)
        self.play(5, 5)
        self.play(10, 10)
        self.assertEqual(self.watches(), [])

    def test_logs_progress_and_parsed_episode(self):
        for t in range(0, 61, 5):
            self.play(1000 + t, t)
        rows = self.watches()
        self.assertEqual(len(rows), 1)
        w = rows[0]
        self.assertEqual((w["season"], w["episode"], w["player"]), (2, 5, "mpv"))
        self.assertAlmostEqual(w["position"], 60)
        self.assertAlmostEqual(w["started_at"], 1000, delta=6)
        self.assertEqual(w["completed"], 0)
        title = self.db.one("SELECT * FROM titles WHERE id = ?", (w["title_id"],))
        self.assertEqual((title["name"], title["kind"]), ("Severance", "show"))
        self.assertEqual(self.matcher.queued, [w["title_id"]])

    def test_paused_time_not_counted(self):
        for t in range(0, 31, 5):
            self.play(t, t)
        for t in range(35, 300, 5):
            self.play(t, 30, paused=True)
        w = self.watches()[0]
        self.assertLess(w["watched_seconds"], 40)

    def test_completion(self):
        for t in range(0, 30, 5):
            self.play(t, t)
        self.play(35, 2900)  # skipped to the end credits
        self.assertEqual(self.watches()[0]["completed"], 1)

    def test_reopen_resumes_then_new_entry_later(self):
        for t in range(0, 30, 5):
            self.play(t, t)
        self.tracker.update([], now=100)  # closed
        for t in range(200, 240, 5):
            self.play(t, 30 + t - 200)
        self.assertEqual(len(self.watches()), 1)
        self.tracker.update([], now=300)
        for t in range(10000, 10040, 5):  # next day
            self.play(t, 100)
        self.assertEqual(len(self.watches()), 2)

    def test_same_show_shares_title(self):
        for t in range(0, 30, 5):
            self.play(t, t)
        for t in range(100, 130, 5):
            self.play(t, t, path="/Videos/Severance/Severance.S02E06.mkv")
        rows = self.watches()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title_id"], rows[1]["title_id"])

    def test_estimated_progress_uses_runtime(self):
        tid = self.matcher.title_for(__import__("tally.parser", fromlist=["parse"]).parse(PATH))
        self.db.execute("UPDATE titles SET runtime = 50 WHERE id = ?", (tid,))
        for t in range(0, 600, 5):
            self.play(t, None, duration=None, paused=None, exact=False)
        w = self.watches()[0]
        self.assertEqual(w["estimated"], 1)
        self.assertEqual(w["duration"], 3000)
        self.assertAlmostEqual(w["position"], 595, delta=6)

    def test_absolute_episode_mapping(self):
        path = "/dl/[SubsPlease] Some Anime - 15 (1080p).mkv"
        for t in range(0, 30, 5):
            self.play(t, t, path=path)
        w = self.watches()[0]
        self.assertEqual((w["season"], w["episode"], w["raw_episode"]), (None, 15, 15))
        cand = Candidate(source="tvmaze", source_id="1", kind="show", name="Some Anime",
                         episodes=[(1, n, f"Ep {n}", None, 24) for n in range(1, 13)]
                         + [(2, n, f"Ep {n}", None, 24) for n in range(1, 13)])
        self.matcher.apply(w["title_id"], cand)
        w = self.watches()[0]
        self.assertEqual((w["season"], w["episode"]), (2, 3))

    def test_merge_on_match(self):
        for t in range(0, 30, 5):
            self.play(t, t, path="/a/The.Office.US.S01E01.mkv")
        for t in range(100, 130, 5):
            self.play(t, t, path="/a/The Office S01E02.mkv")
        a, b = [r["title_id"] for r in self.watches()]
        self.assertNotEqual(a, b)
        cand = Candidate(source="tvmaze", source_id="526", kind="show", name="The Office")
        self.matcher.apply(a, cand)
        self.matcher.apply(b, cand)
        a, b = [r["title_id"] for r in self.watches()]
        self.assertEqual(a, b)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM titles")["n"], 1)


class PollTests(unittest.TestCase):
    def test_exact_source_wins(self):
        class A(Source):
            def poll(self):
                return [PlaybackState("IINA", "/x/a.mkv", 10, 100, False)]

        class B(Source):
            def poll(self):
                return [PlaybackState("IINA", "/x/a.mkv", exact=False), PlaybackState("QT", "/y/b.mp4", exact=False)]

        states = poll_all([A(), B()])
        self.assertEqual([(s.player, s.exact) for s in states], [("IINA", True), ("QT", False)])


if __name__ == "__main__":
    unittest.main()
