import tempfile
import unittest
from pathlib import Path

from tally.db import Database
from tally.matcher import existing_title_for


class ExistingTitleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.zero = self.db.execute(
            "INSERT INTO titles(kind, name, matched, created_at) VALUES('show', ?, 1, 0)",
            ("Re:ZERO -Starting Life in Another World-",)).lastrowid

    def tearDown(self):
        self.tmp.cleanup()

    def test_short_name_finds_existing_show(self):
        self.assertEqual(existing_title_for(self.db, "show", "Re Zero", -1), self.zero)

    def test_extra_words_do_not_match(self):
        self.assertIsNone(existing_title_for(self.db, "show", "Re Zero Break Time", -1))

    def test_other_kind_and_the_placeholder_are_skipped(self):
        self.assertIsNone(existing_title_for(self.db, "movie", "Re Zero", -1))
        self.assertIsNone(existing_title_for(self.db, "show", "Re Zero", self.zero))


if __name__ == "__main__":
    unittest.main()
