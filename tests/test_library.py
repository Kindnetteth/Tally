import tempfile
import unittest
from pathlib import Path

from tally import library
from tally.db import Database
from tally.parser import parse
from tests.test_tracker import FakeMatcher


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Downloads"
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.matcher = FakeMatcher(self.db)
        library._cache.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, rel):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return str(path)

    def watched(self, path, season_sizes=None):
        title_id = self.matcher.title_for(parse(path))
        p = parse(path)
        self.db.execute("INSERT INTO watches(path, filename, title_id, season, episode, started_at, last_seen_at, "
                        "completed) VALUES(?, ?, ?, ?, ?, 0, 0, 1)", (path, p.filename, title_id, p.season, p.episode))
        for season, size in (season_sizes or {}).items():
            for n in range(1, size + 1):
                self.db.execute("INSERT OR IGNORE INTO episodes(title_id, season, number) VALUES(?,?,?)",
                                (title_id, season, n))
        return title_id

    def test_next_episode_in_same_folder(self):
        last = self.touch("Show.S01.1080p/Show.S01E01.1080p.mkv")
        nxt = self.touch("Show.S01.1080p/Show.S01E02.1080p.mkv")
        tid = self.watched(last)
        self.assertEqual(library.find_episode(self.db, tid, 1, 2), nxt)
        self.assertIsNone(library.find_episode(self.db, tid, 1, 3))

    def test_next_season_in_sibling_folder(self):
        last = self.touch("[Group] Show/Season 01/[Group] Show - 12 [1080p].mkv")
        nxt = self.touch("[Group] Show/Season 02/[Group] Show - 01 [1080p].mkv")
        tid = self.watched(last, {1: 12, 2: 12})
        self.assertEqual(library.find_episode(self.db, tid, 2, 1), nxt)

    def test_new_season_pack_with_absolute_numbers(self):
        last = self.touch("Kaiju.No.8.S01.1080p.WEB-DL/Kaiju.No.8.S01E12.1080p.WEB-DL.mkv")
        nxt = self.touch("[Anime Time] Kaiju No. 8 (Season 2) [1080p]/Kaiju No. 8 - 13 [1080p].mkv")
        tid = self.watched(last, {1: 12, 2: 11})
        self.assertEqual(library.find_episode(self.db, tid, 2, 1), nxt)

    def test_only_offered_files_play(self):
        other = self.touch("Somewhere/Other.Show.S01E01.1080p.mkv")
        with self.assertRaises(ValueError):
            library.play(other)


if __name__ == "__main__":
    unittest.main()
