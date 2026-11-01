import unittest
from unittest import mock

from tally import metadata


def fake_tmdb(path, key, params=None):
    if path == "/find/tt0903747":
        return {"tv_results": [{"id": 1396}]}
    if path == "/1396/recommendations" or path == "/tv/1396/recommendations":
        return {"results": [{"id": 60059, "name": "Better Call Saul", "first_air_date": "2015-02-08",
                             "poster_path": "/p.jpg", "overview": "..."}]}
    if path == "/search/movie":
        return {"results": [{"id": 27205}]}
    if path == "/movie/27205/recommendations":
        return {"results": []}
    if path == "/movie/27205/similar":
        return {"results": [{"id": 157336, "title": "Interstellar", "release_date": "2014-11-05"}]}
    raise AssertionError(path)


class SimilarTests(unittest.TestCase):
    @mock.patch.object(metadata, "_tmdb", side_effect=fake_tmdb)
    def test_show_found_by_imdb_id(self, _):
        items = metadata.tmdb_recommendations("show", "Breaking Bad", 2008, "tt0903747", "tvmaze", "169", "k")
        self.assertEqual([(i["name"], i["year"], i["kind"]) for i in items], [("Better Call Saul", 2015, "show")])
        self.assertEqual(items[0]["url"], "https://www.themoviedb.org/tv/60059")

    @mock.patch.object(metadata, "_tmdb", side_effect=fake_tmdb)
    def test_movie_by_name_falls_back_to_similar(self, _):
        items = metadata.tmdb_recommendations("movie", "Inception", 2010, None, "imdb", "tt1375666", "k")
        self.assertEqual([i["name"] for i in items], ["Interstellar"])
        self.assertIsNone(items[0]["poster"])


if __name__ == "__main__":
    unittest.main()
