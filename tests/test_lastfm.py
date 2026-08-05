"""Dependency-free tests for the Last.fm loved-tracks signal.

Covers the pure data-shaping only — key normalisation, response parsing and the
in-place loved marking — plus the enabled/degradation gates, using no live API.

Run from the repo root:
    python -m unittest tests.test_lastfm        # stdlib only
    python -m pytest tests/test_lastfm.py        # if pytest is installed
"""

import os
import unittest

from backend.lastfm_client import (
    LastfmClient, loved_key, mark_loved, normalise_name,
    parse_loved_tracks, parse_similar_artists,
)


class TestLovedKey(unittest.TestCase):
    def test_folds_case_articles_and_punctuation(self):
        # "The Beatles" / "Beatles", accents and punctuation all collapse together
        self.assertEqual(loved_key("The Beatles", "Hey Jude"), loved_key("beatles", "hey jude"))
        self.assertEqual(loved_key("Beyoncé", "Halo"), loved_key("beyonce", "halo!"))

    def test_distinct_tracks_stay_distinct(self):
        self.assertNotEqual(loved_key("Oasis", "Wonderwall"), loved_key("Oasis", "Supersonic"))

    def test_handles_missing_values(self):
        self.assertEqual(loved_key(None, None), ("", ""))


class TestParseLovedTracks(unittest.TestCase):
    def test_parses_a_list(self):
        data = {"lovedtracks": {"track": [
            {"name": "Hey Jude", "artist": {"name": "The Beatles"}, "mbid": "abc"},
            {"name": "Halo", "artist": {"name": "Beyoncé"}, "mbid": ""},
        ]}}
        rows = parse_loved_tracks(data)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"artist": "The Beatles", "title": "Hey Jude", "mbid": "abc"})

    def test_single_track_object_coerced_to_list(self):
        data = {"lovedtracks": {"track": {"name": "Halo", "artist": {"name": "Beyoncé"}}}}
        rows = parse_loved_tracks(data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Halo")

    def test_no_loved_tracks(self):
        self.assertEqual(parse_loved_tracks({"lovedtracks": {}}), [])

    def test_artist_as_bare_string(self):
        data = {"lovedtracks": {"track": [{"name": "Halo", "artist": "Beyoncé"}]}}
        rows = parse_loved_tracks(data)
        self.assertEqual(rows[0]["artist"], "Beyoncé")


class TestMarkLoved(unittest.TestCase):
    def _tracks(self):
        return [
            {"id": "1", "artist": "The Beatles", "title": "Hey Jude"},
            {"id": "2", "artist": "Oasis", "title": "Wonderwall"},
        ]

    def test_marks_only_matching_tracks(self):
        tracks = self._tracks()
        loved = {loved_key("beatles", "hey jude")}
        count = mark_loved(tracks, loved)
        self.assertEqual(count, 1)
        self.assertTrue(tracks[0]["loved"])
        self.assertNotIn("loved", tracks[1])

    def test_empty_loved_set_is_a_noop(self):
        tracks = self._tracks()
        self.assertEqual(mark_loved(tracks, set()), 0)
        self.assertNotIn("loved", tracks[0])


class TestParseSimilarArtists(unittest.TestCase):
    def test_parses_and_preserves_rank_order(self):
        data = {"similarartists": {"artist": [
            {"name": "Portishead", "mbid": "m1", "match": "1.0"},
            {"name": "Massive Attack", "mbid": "", "match": "0.8"},
        ]}}
        rows = parse_similar_artists(data)
        self.assertEqual([r["name"] for r in rows], ["Portishead", "Massive Attack"])
        self.assertEqual(rows[0]["match"], "1.0")

    def test_single_artist_object(self):
        data = {"similarartists": {"artist": {"name": "Portishead", "match": "1.0"}}}
        self.assertEqual(len(parse_similar_artists(data)), 1)

    def test_none_and_nameless_skipped(self):
        self.assertEqual(parse_similar_artists({"similarartists": {}}), [])
        data = {"similarartists": {"artist": [{"name": "  "}, {"name": "Real"}]}}
        self.assertEqual([r["name"] for r in parse_similar_artists(data)], ["Real"])


class TestNormaliseName(unittest.TestCase):
    def test_matches_across_articles_and_accents(self):
        self.assertEqual(normalise_name("The Beatles"), normalise_name("beatles"))
        self.assertEqual(normalise_name("Sigur Rós"), normalise_name("sigur ros"))

    def test_distinct_names_stay_distinct(self):
        self.assertNotEqual(normalise_name("Oasis"), normalise_name("Blur"))


class TestEnabledGates(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("LASTFM_API_KEY", "LASTFM_USERNAME")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_disabled_without_key(self):
        client = LastfmClient()
        self.assertFalse(client.enabled)
        self.assertFalse(client.user_enabled)

    def test_global_enabled_with_key_only(self):
        os.environ["LASTFM_API_KEY"] = "k"
        client = LastfmClient()
        self.assertTrue(client.enabled)
        self.assertFalse(client.user_enabled)  # no username → no user reads

    def test_user_enabled_with_key_and_username(self):
        os.environ["LASTFM_API_KEY"] = "k"
        os.environ["LASTFM_USERNAME"] = "rick"
        client = LastfmClient()
        self.assertTrue(client.user_enabled)


class TestLovedTrackKeysDegrades(unittest.IsolatedAsyncioTestCase):
    async def test_returns_empty_when_not_configured(self):
        for k in ("LASTFM_API_KEY", "LASTFM_USERNAME"):
            os.environ.pop(k, None)
        client = LastfmClient()
        self.assertEqual(await client.loved_track_keys(), set())


if __name__ == "__main__":
    unittest.main()
