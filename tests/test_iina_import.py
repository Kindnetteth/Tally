import plistlib
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from tally.db import Database
from tally.iina_import import NS_EPOCH, import_history, looks_like_release, read_history
from tests.test_tracker import FakeMatcher


def write_history(base: Path, items):
    """Write a history.plist shaped like IINA's NSKeyedArchiver output."""
    objects = ["$null", None]
    refs = []
    for path, opened, duration, md5 in items:
        url = "file://" + urllib.parse.quote(path)
        objects.append({"NS.relative": plistlib.UID(len(objects) + 1), "NS.base": plistlib.UID(0)})
        objects.append(url)
        url_ref = plistlib.UID(len(objects) - 2)
        objects.append({"NS.time": opened - NS_EPOCH})
        date_ref = plistlib.UID(len(objects) - 1)
        objects.append({"IINAPHUrl": url_ref, "IINAPHDate": date_ref, "IINAPHDuration": duration,
                        "IINAPHMpvmd5": md5, "IINAPHNme": Path(path).name, "IINAPHPlayed": True})
        refs.append(plistlib.UID(len(objects) - 1))
    objects[1] = {"NS.objects": refs}
    archive = {"$archiver": "NSKeyedArchiver", "$version": 100000, "$top": {"root": plistlib.UID(1)},
               "$objects": objects}
    (base / "history.plist").write_bytes(plistlib.dumps(archive, fmt=plistlib.FMT_BINARY))
    (base / "watch_later").mkdir(exist_ok=True)


class IinaImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.db = Database(self.base / "t.db")
        self.matcher = FakeMatcher(self.db)
        write_history(self.base, [
            ("/Videos/Severance.S02E05.1080p.mkv", 1_780_000_000, 3000.0, "aa11"),   # stopped halfway
            ("/Videos/Severance.S02E06.1080p.mkv", 1_779_900_000, 3000.0, "bb22"),   # no resume point, seen before
            ("/Videos/Inception.2010.1080p.mkv", 1_780_200_000, 8880.0, "cc33"),     # peeked at
            ("/Projects/03_EXPORTS/08 Final Cut.mp4", 1_780_300_000, 1800.0, "dd44"),  # own edit
        ])
        (self.base / "watch_later" / "AA11").write_text("# redirect entry\nstart=1500.250000\n")
        (self.base / "watch_later" / "CC33").write_text("start=4.000000\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_entries(self):
        entries = {Path(e.path).name: e for e in read_history(self.base)}
        self.assertEqual(entries["Severance.S02E05.1080p.mkv"].position, 1500.25)
        self.assertIsNone(entries["Severance.S02E06.1080p.mkv"].position)
        self.assertEqual(entries["Severance.S02E05.1080p.mkv"].opened_at, 1_780_000_000)

    def test_import(self):
        self.assertEqual(import_history(self.db, self.matcher, self.base), 2)
        rows = {r["filename"]: r for r in self.db.query("SELECT * FROM watches")}
        half = rows["Severance.S02E05.1080p.mkv"]
        self.assertEqual((half["season"], half["episode"], half["completed"]), (2, 5, 0))
        self.assertEqual(half["position"], 1500.25)
        self.assertEqual(half["started_at"], 1_780_000_000)
        done = rows["Severance.S02E06.1080p.mkv"]
        self.assertEqual((done["completed"], done["estimated"]), (1, 1))
        self.assertNotIn("Inception.2010.1080p.mkv", rows)

    def test_only_releases_count(self):
        for path in ("/D/Thunder.3.S01E02.MULTIVERSE.1080p.NF.WEB-DL.mkv",
                     "/D/Daemons Of The Shadow Realm - S01E04 - Jin And Yuru.mkv",
                     "/D/[DB]Dandadan 2nd Season_-_01_(Dual Audio_10bit_1080p_x265).mkv",
                     "/D/Zombieland (2009) [1080p] {5.1}/Zombieland.BluRay.1080p.x264.5.1.Judas.mp4",
                     "/D/Hoppers 2026 1080p DCP Line Audio H264-DJT.mkv",
                     "/D/(CBB) Tokyo Ghoul - The Complete Series/01. Tokyo Ghoul/(CBB) Tokyo Ghoul - 01 (1080p)(10bit-x265-AAC).mkv"):
            self.assertTrue(looks_like_release(path), path)
        for path in ("/P/03_EXPORTS/08 Final Cut.mp4", "/P/Footage/B Cam/As You Like It.mp4",
                     "/D/Portfolio - Reel - 92.mp4", "/D/clip_download_1787033984618.mp4", "/R/2026-10-13 20-16-01.mkv",
                     "/M/IMG_5334.mov", "/M/song.mp3", "/M/Saved - @user_dd_25 (1).mp4",
                     "/D/Show (BD 1080p)/Extra/NCED - 01.mkv",
                     "/D/[Anime Time] Kaiju No. 8 [1080p]/NC/Kaiju No. 8 - NCOP.mkv",
                     "/D/Movie.2018.2160p.BluRay.x265/Movie.2018.2160p.BluRay.sample.mkv",
                     "/D/Music Production Course/Masterclass Complete.mkv"):
            self.assertFalse(looks_like_release(path), path)

    def test_reopened_episode_counts_when_you_moved_on(self):
        write_history(self.base, [
            ("/D/Show.S01E01.1080p.mkv", 1_780_000_000, 1440.0, "e1"),   # reopened later, position wiped
            ("/D/Show.S01E02.1080p.mkv", 1_780_010_000, 1440.0, "e2"),   # the one you stopped in
        ])
        (self.base / "watch_later" / "E1").write_text("start=25.0\n")
        (self.base / "watch_later" / "E2").write_text("start=600.0\n")
        import_history(self.db, self.matcher, self.base)
        done = {r["episode"]: r["completed"] for r in self.db.query("SELECT episode, completed FROM watches")}
        self.assertEqual(done, {1: 1, 2: 0})

    def test_import_twice_adds_nothing(self):
        import_history(self.db, self.matcher, self.base)
        self.assertEqual(import_history(self.db, self.matcher, self.base), 0)


class AlternateNameTests(unittest.TestCase):
    def test_other_name_in_parentheses(self):
        from tally.matcher import alternate_names

        self.assertEqual(alternate_names(
            "/D/[Judas] Kill Ao (Kill Blue) (Season 01) [1080p][HEVC x265 10bit]/[Judas] Kill Ao - S01E07.mkv",
            "Kill Ao"), ["Kill Blue"])
        self.assertEqual(alternate_names(
            "/D/(CBB) Tokyo Ghoul (BD 1080p)(Dual-Audio)/(CBB) Tokyo Ghoul - 01 (1080p)(10bit-x265-AAC).mkv",
            "Tokyo Ghoul"), [])


if __name__ == "__main__":
    unittest.main()
