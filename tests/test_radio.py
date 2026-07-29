"""Lightweight, dependency-free tests for the Radio feature.

Covers the pure logic only — candidate pooling/dedup, seed resolution and the
AI response parsing — using fakes so no live Navidrome server or AI provider is
required.

Run from the repo root:
    python -m unittest tests.test_radio        # stdlib only
    python -m pytest tests/test_radio.py       # if pytest is installed
"""

import unittest

from backend.radio import RadioProcessor, MAX_SIMILAR_ARTISTS
from backend.ai_client import AIClient


def _track(tid, title="T", artist="A", album="Alb", year=2000, genre="Rock", play_count=0):
    return {
        "id": tid, "title": title, "artist": artist, "album": album,
        "year": year, "genre": genre, "play_count": play_count,
        "local_library_likes": False,
    }


class FakeNav:
    """Minimal async stand-in for NavidromeClient, with call tracking."""

    def __init__(self, artist_tracks=None, similar=None, genre_tracks=None,
                 song=None, artists=None):
        self.artist_tracks = artist_tracks or {}   # artist_id -> [tracks]
        self.similar = similar or []                # [{id, name}]
        self.genre_tracks = genre_tracks or {}      # genre -> [tracks]
        self.song = song                            # dict for get_song
        self.artists = artists or []                # [{id, name}]
        self.genre_called = False

    async def get_tracks_by_artist(self, artist_id, library_ids=None):
        return list(self.artist_tracks.get(artist_id, []))

    async def get_similar_artists(self, artist_id, count=20):
        return list(self.similar)

    async def get_tracks_by_genre(self, genre, library_ids=None):
        self.genre_called = True
        return list(self.genre_tracks.get(genre, []))

    async def get_song(self, song_id):
        if not self.song:
            raise Exception(f"Song not found: {song_id}")
        return self.song

    async def get_artists(self, library_ids=None):
        return list(self.artists)


class FakeProvider:
    """Stand-in AI provider returning a canned response string."""

    def __init__(self, response, provider_type="google"):
        self._response = response
        self.provider_type = provider_type
        self.api_key = "x"
        self.model = "test"
        self.base_url = None

    async def generate(self, system_prompt, user_prompt, max_tokens, temperature):
        return self._response


def _make_ai_client(provider):
    """Build an AIClient without running __init__ (which reads env/providers)."""
    client = AIClient.__new__(AIClient)
    client.provider = provider
    client.api_key = provider.api_key
    client.model = "test-model"
    client.base_url = None
    return client


class RadioProcessorTests(unittest.IsolatedAsyncioTestCase):

    async def test_pool_dedupes_across_seed_and_similar(self):
        nav = FakeNav(
            artist_tracks={
                "A1": [_track("t1"), _track("t2")],
                "A2": [_track("t3"), _track("t2")],  # t2 duplicates the seed's
            },
            similar=[{"id": "A2", "name": "Beta"}],
        )
        proc = RadioProcessor(nav)
        seed = {"type": "artist", "id": "A1", "name": "Alpha",
                "artist_id": "A1", "artist_name": "Alpha", "genre": None}
        pool = await proc.gather_candidate_tracks(seed)
        ids = [t["id"] for t in pool]

        self.assertEqual(len(ids), len(set(ids)), "pool must be de-duplicated")
        self.assertIn("t3", ids, "similar-artist tracks should be included")
        self.assertEqual(sorted(ids), ["t1", "t2", "t3"])

    async def test_genre_fallback_used_when_pool_is_thin(self):
        nav = FakeNav(
            artist_tracks={"A1": [_track("t1", genre="Jazz")]},
            similar=[],  # no similar artists available
            genre_tracks={"Jazz": [_track("g1", genre="Jazz"), _track("t1", genre="Jazz")]},
        )
        proc = RadioProcessor(nav)
        seed = {"type": "artist", "id": "A1", "name": "Alpha",
                "artist_id": "A1", "artist_name": "Alpha", "genre": None}
        pool = await proc.gather_candidate_tracks(seed)
        ids = [t["id"] for t in pool]

        self.assertTrue(nav.genre_called, "genre fallback should trigger on a thin pool")
        self.assertIn("g1", ids)
        self.assertEqual(len(ids), len(set(ids)))

    async def test_genre_fallback_skipped_when_pool_is_large(self):
        big = [_track(f"t{i}") for i in range(60)]
        nav = FakeNav(artist_tracks={"A1": big}, similar=[])
        proc = RadioProcessor(nav)
        seed = {"type": "artist", "id": "A1", "name": "Alpha",
                "artist_id": "A1", "artist_name": "Alpha", "genre": None}
        pool = await proc.gather_candidate_tracks(seed)

        self.assertFalse(nav.genre_called, "no genre fallback needed with a full pool")
        self.assertEqual(len(pool), 60)

    async def test_resolve_artist_seed(self):
        nav = FakeNav(artists=[{"id": "A1", "name": "Alpha"}])
        proc = RadioProcessor(nav)
        seed = await proc.resolve_seed("artist", "A1")
        self.assertEqual(seed["type"], "artist")
        self.assertEqual(seed["name"], "Alpha")
        self.assertEqual(seed["artist_id"], "A1")

    async def test_resolve_artist_seed_not_found(self):
        nav = FakeNav(artists=[{"id": "A1", "name": "Alpha"}])
        proc = RadioProcessor(nav)
        with self.assertRaises(Exception):
            await proc.resolve_seed("artist", "NOPE")

    async def test_resolve_song_seed(self):
        nav = FakeNav(song={
            "id": "S1", "title": "Karma Police", "artist": "Radiohead",
            "artist_id": "A9", "genre": "Alt Rock", "play_count": 3,
        })
        proc = RadioProcessor(nav)
        seed = await proc.resolve_seed("song", "S1")
        self.assertEqual(seed["type"], "song")
        self.assertEqual(seed["song_title"], "Karma Police")
        self.assertEqual(seed["name"], "Karma Police — Radiohead")
        self.assertEqual(seed["artist_id"], "A9")
        self.assertEqual(seed["genre"], "Alt Rock")

    def test_most_common_genre(self):
        tracks = [_track("1", genre="Rock"), _track("2", genre="Rock"), _track("3", genre="Jazz")]
        self.assertEqual(RadioProcessor._most_common_genre(tracks), "Rock")
        self.assertIsNone(RadioProcessor._most_common_genre([_track("1", genre=None)]))


class CurateRadioTests(unittest.IsolatedAsyncioTestCase):

    def _candidates(self):
        return [
            _track("c0", play_count=5),
            _track("c1", play_count=10),
            _track("c2", play_count=1),
        ]

    async def test_happy_path_maps_indices_and_sanitises_suggestions(self):
        response = (
            '{"track_ids": [2, 0, 2, 5], "reasoning": "nice", '
            '"album_suggestions": ['
            '{"artist": "X", "album": "Y", "year": 2001, "reason": "fits"}, '
            '{"artist": "", "album": "Missing artist"}, '
            '{"album": "No artist key"}]}'
        )
        client = _make_ai_client(FakeProvider(response))
        track_ids, reasoning, suggestions = await client.curate_radio(
            seed_name="Alpha", tracks_json=self._candidates(), num_tracks=25
        )

        # indices 2 and 0 are valid+unique; 2 is a dup, 5 is out of range -> 2 tracks
        self.assertEqual(len(track_ids), 2)
        self.assertEqual(len(track_ids), len(set(track_ids)), "no duplicate track ids")
        self.assertTrue(set(track_ids).issubset({"c0", "c1", "c2"}))
        self.assertEqual(reasoning, "nice")
        # only the first suggestion is well-formed
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["artist"], "X")
        self.assertEqual(suggestions[0]["album"], "Y")

    async def test_no_api_key_falls_back_to_play_count(self):
        provider = FakeProvider("unused", provider_type="openrouter")
        client = _make_ai_client(provider)
        client.api_key = None  # simulate missing key

        track_ids, reasoning, suggestions = await client.curate_radio(
            seed_name="Alpha", tracks_json=self._candidates(), num_tracks=2
        )
        # highest play_count first: c1 (10), c0 (5)
        self.assertEqual(track_ids, ["c1", "c0"])
        self.assertEqual(suggestions, [])
        self.assertIn("Fallback", reasoning)

    async def test_malformed_json_falls_back(self):
        client = _make_ai_client(FakeProvider("this is not json"))
        track_ids, reasoning, suggestions = await client.curate_radio(
            seed_name="Alpha", tracks_json=self._candidates(), num_tracks=2
        )
        self.assertEqual(len(track_ids), 2)
        self.assertEqual(suggestions, [])

    def test_sanitise_album_suggestions(self):
        client = AIClient.__new__(AIClient)
        raw = [
            {"artist": "A", "album": "B", "year": 1999, "reason": "r"},
            {"artist": "  ", "album": "B"},          # blank artist -> dropped
            {"artist": "C"},                          # missing album -> dropped
            "not a dict",                             # -> dropped
        ] + [{"artist": f"A{i}", "album": f"B{i}"} for i in range(10)]  # overflow
        cleaned = client._sanitise_album_suggestions(raw)
        self.assertLessEqual(len(cleaned), 5, "suggestions capped at 5")
        self.assertEqual(cleaned[0]["artist"], "A")

    def test_fallback_selection_sorts_by_play_count(self):
        client = AIClient.__new__(AIClient)
        track_ids, reasoning, suggestions = client._fallback_radio_selection(
            self._candidates(), num_tracks=3
        )
        self.assertEqual(track_ids, ["c1", "c0", "c2"])
        self.assertEqual(suggestions, [])


if __name__ == "__main__":
    unittest.main()
