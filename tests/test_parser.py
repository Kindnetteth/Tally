import unittest

from tally.parser import is_video_path, normalize, parse


def p(path):
    r = parse(path)
    return (r.title, r.kind, r.year, r.season, r.episode)


class ShowTests(unittest.TestCase):
    def test_standard_release(self):
        self.assertEqual(p("Show.S02E05.1080p.mkv"), ("Show", "show", None, 2, 5))
        self.assertEqual(p("The.Last.of.Us.S01E03.Long.Long.Time.2160p.HMAX.WEB-DL.x265-GROUP.mkv"),
                         ("The Last of Us", "show", None, 1, 3))

    def test_episode_title(self):
        r = parse("The.Last.of.Us.S01E03.Long.Long.Time.2160p.HMAX.WEB-DL.x265-GROUP.mkv")
        self.assertEqual(r.episode_title, "Long Long Time")

    def test_lowercase_underscores(self):
        self.assertEqual(p("breaking_bad_s05e14_ozymandias.avi"), ("breaking bad", "show", None, 5, 14))

    def test_multi_episode(self):
        r = parse("Friends.S03E24E25.720p.mkv")
        self.assertEqual((r.season, r.episode, r.episode_end), (3, 24, 25))
        r = parse("Friends S03E24-E25.mkv")
        self.assertEqual((r.season, r.episode, r.episode_end), (3, 24, 25))

    def test_x_format(self):
        self.assertEqual(p("Futurama - 2x05 - Put Your Head on My Shoulders.mp4"),
                         ("Futurama", "show", None, 2, 5))

    def test_words(self):
        self.assertEqual(p("Avatar The Last Airbender Season 1 Episode 12.mp4"),
                         ("Avatar The Last Airbender", "show", None, 1, 12))

    def test_year_in_show_name(self):
        self.assertEqual(p("Doctor.Who.2005.S01E01.Rose.mkv"), ("Doctor Who", "show", 2005, 1, 1))

    def test_acronym_title(self):
        self.assertEqual(p("Marvels.Agents.of.S.H.I.E.L.D.S01E01.mkv")[0], "Marvels Agents of S.H.I.E.L.D")

    def test_daily_show(self):
        r = parse("The.Daily.Show.2024.03.15.Guest.720p.WEB.mkv")
        self.assertEqual((r.title, r.kind, r.air_date), ("The Daily Show", "show", "2024-03-15"))

    def test_anime_dash(self):
        self.assertEqual(p("[SubsPlease] Frieren - 12 (1080p) [ABCD1234].mkv"),
                         ("Frieren", "show", None, None, 12))
        self.assertEqual(p("[Erai-raws] One Piece - 1050 [1080p][Multiple Subtitle].mkv"),
                         ("One Piece", "show", None, None, 1050))

    def test_anime_in_season_folder(self):
        path = ("/Users/me/Downloads/[Breeze] Dr. STONE [1080p BD][AV1][dual audio]/Season 02/"
                "[Breeze] Dr. STONE - Stone Wars - 04 [1080p BD][AV1][dual audio].mkv")
        self.assertEqual(p(path), ("Dr. STONE - Stone Wars", "show", None, 2, 4))

    def test_anime_no_dash(self):
        self.assertEqual(p("[Group] Mushishi 05 [720p].mkv"), ("Mushishi", "show", None, None, 5))

    def test_folder_library(self):
        self.assertEqual(p("/TV/The Wire/Season 3/05 - Straight and True.mkv"),
                         ("The Wire", "show", None, 3, 5))
        self.assertEqual(p("C:\\Videos\\Severance\\S02\\S02E05.mkv"), ("Severance", "show", None, 2, 5))
        self.assertEqual(p("/TV/Severance (2022)/Season 01/Episode 3.mkv"), ("Severance", "show", 2022, 1, 3))

    def test_season_pack_folder(self):
        self.assertEqual(p("/dl/Show.Name.S02.1080p.WEB-DL/E05.mkv"), ("Show Name", "show", None, None, 5))

    def test_star_wars_episode_is_movie(self):
        self.assertEqual(p("Star.Wars.Episode.4.A.New.Hope.1977.1080p.mkv")[1], "movie")


class MovieTests(unittest.TestCase):
    def test_release(self):
        self.assertEqual(p("Inception.2010.1080p.BluRay.x264-SPARKS.mkv"), ("Inception", "movie", 2010, None, None))

    def test_parenthesised_year(self):
        self.assertEqual(p("Spirited Away (2001).mp4"), ("Spirited Away", "movie", 2001, None, None))

    def test_number_titles(self):
        self.assertEqual(p("2012.2009.720p.mkv"), ("2012", "movie", 2009, None, None))
        self.assertEqual(p("1917.mkv"), ("1917", "movie", None, None, None))
        self.assertEqual(p("Blade.Runner.2049.2017.2160p.mkv"), ("Blade Runner 2049", "movie", 2017, None, None))
        self.assertEqual(p("2001.A.Space.Odyssey.1968.mkv"), ("2001 A Space Odyssey", "movie", 1968, None, None))

    def test_no_year(self):
        self.assertEqual(p("Arrival.1080p.WEBRip.mkv"), ("Arrival", "movie", None, None, None))
        self.assertEqual(p("The Matrix.mkv"), ("The Matrix", "movie", None, None, None))

    def test_site_prefix_and_brackets(self):
        self.assertEqual(p("www.Torrenting.com - Dune.Part.Two.2024.1080p.mkv"),
                         ("Dune Part Two", "movie", 2024, None, None))

    def test_words_that_look_like_junk(self):
        self.assertEqual(p("Real.Steel.2011.mkv")[0], "Real Steel")
        self.assertEqual(p("The.Real.Housewives.S01E01.mkv")[0], "The Real Housewives")
        self.assertEqual(p("Mr.Hollands.Opus.1995.mkv")[0], "Mr Hollands Opus")

    def test_generic_name_uses_folder(self):
        self.assertEqual(p("/Movies/Heat (1995)/movie.mkv"), ("Heat", "movie", 1995, None, None))

    def test_plain_folders_are_ignored(self):
        self.assertEqual(p("/Users/me/Downloads/1917.mkv"), ("1917", "movie", None, None, None))
        self.assertEqual(p("/Users/me/Movies/video.mp4")[0], "video")

    def test_movie_dash_year_is_not_an_episode(self):
        self.assertEqual(p("Alien - 1979.mkv"), ("Alien", "movie", 1979, None, None))


class HelperTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("The Lord of the Rings: The Two Towers"), "lord of the rings the two towers")
        self.assertEqual(normalize("Grey's Anatomy"), normalize("Greys Anatomy"))

    def test_is_video(self):
        self.assertTrue(is_video_path("/a/b/c.MKV"))
        self.assertFalse(is_video_path("/a/b/c.srt"))


if __name__ == "__main__":
    unittest.main()
