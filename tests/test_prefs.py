import tempfile
import unittest
from pathlib import Path

from tally import prefs
from tally.db import Database, is_complete
from tally.players import PlaybackState
from tally.tracker import Tracker
from tests.test_tracker import FakeMatcher

SHOW = "/Videos/Cyberpunk.Edgerunners.S02E03.1080p.WEB-DL.mkv"


class PrefsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.tracker = Tracker(self.db, FakeMatcher(self.db))

    def tearDown(self):
        self.tmp.cleanup()

    def play(self, path, t, position, duration=1690.0):
        self.tracker.update([PlaybackState(player="IINA", path=path, position=position, duration=duration,
                                           paused=False, exact=True)], now=t)

    def test_defaults_and_validation(self):
        self.assertEqual(prefs.load(self.db)["accent"], "orchid")
        self.assertEqual(prefs.save(self.db, {"accent": "ocean", "nonsense": 1})["accent"], "ocean")
        with self.assertRaises(ValueError):
            prefs.save(self.db, {"theme": "neon"})
        self.assertEqual(prefs.icon_name(prefs.load(self.db)), "ocean")
        self.assertEqual(prefs.icon_name(prefs.save(self.db, {"app_icon": "midnight"})), "midnight")

    def test_credits_count_as_finished(self):
        # Stopped at 88%, with 3 minutes of credits left
        self.assertTrue(is_complete(1490, 1690))
        self.assertFalse(is_complete(1490, 1690, *prefs.finish_rule({"finish_at": 95})))

    def test_recheck_finishes_old_entries(self):
        self.db.execute("INSERT INTO watches(path, filename, started_at, last_seen_at, max_position, duration) "
                        "VALUES('/a.mkv', 'a.mkv', 0, 0, 1490, 1690)")
        self.assertEqual(self.db.recheck_completed(*prefs.finish_rule(prefs.load(self.db))), 1)

    def test_paused_logs_nothing(self):
        prefs.save(self.db, {"paused": True})
        for t in range(0, 120, 5):
            self.play(SHOW, t, t)
        self.assertEqual(self.db.query("SELECT * FROM watches"), [])

    def test_ignored_folder_and_non_releases(self):
        prefs.save(self.db, {"ignored_folders": ["/Videos/Private"]})
        for t in range(0, 120, 5):
            self.tracker.update([
                PlaybackState(player="IINA", path="/Videos/Private/Show.S01E01.1080p.mkv", position=t, duration=1500,
                              paused=False, exact=True),
                PlaybackState(player="IINA", path="/Projects/03_EXPORTS/08 Final Cut.mp4", position=t, duration=1500,
                              paused=False, exact=True),
                PlaybackState(player="IINA", path=SHOW, position=t, duration=1690, paused=False, exact=True),
            ], now=t)
        self.assertEqual([r["path"] for r in self.db.query("SELECT path FROM watches")], [SHOW])

    def test_plex_style_names_still_log(self):
        from tally.parser import looks_like_release

        self.assertTrue(looks_like_release("/Movies/Arrival (2016)/Arrival (2016).mkv"))
        self.assertTrue(looks_like_release("/TV/Severance/Season 2/05 - Trojan's Horse.mkv"))
        self.assertTrue(looks_like_release(
            "/Downloads/[Anime Time] Re Zero Season 02/[Anime Time] Re Zero Season 02 - 01.mkv"))
        self.assertTrue(looks_like_release("/TV/[Group] Show/Season 2/05.mkv"))
        self.assertFalse(looks_like_release("/Movies/Phone/Birthday.mov"))
        self.assertFalse(looks_like_release("/Movies/Phone/Season 2.mov"))


if __name__ == "__main__":
    unittest.main()
