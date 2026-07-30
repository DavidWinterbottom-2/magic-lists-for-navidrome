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
from backend.navidrome_client import NavidromeClient


def _track(tid, title="T", artist="A", album="Alb", year=2000, genre="Rock", play_count=0):
    return {
        "id": tid, "title": title, "artist": artist, "album": album,
        "year": year, "genre": genre, "play_count": play_count,
        "local_library_likes": False,
    }


class FakeNav:
    """Minimal async stand-in for NavidromeClient, with call tracking."""

    def __init__(self, artist_tracks=None, similar=None, genre_tracks=None,
                 song=None, artists=None, similar_songs=None):
        self.artist_tracks = artist_tracks or {}   # artist_id -> [tracks]
        self.similar = similar or []                # [{id, name}]
        self.genre_tracks = genre_tracks or {}      # genre -> [tracks]
        self.song = song                            # dict for get_song
        self.artists = artists or []                # [{id, name}]
        self.similar_songs = similar_songs or {}    # artist_id -> [tracks]
        self.genre_called = False
        self.similar_songs_called = False

    async def get_tracks_by_artist(self, artist_id, library_ids=None):
        return list(self.artist_tracks.get(artist_id, []))

    async def get_similar_artists(self, artist_id, count=20):
        return list(self.similar)

    async def get_similar_songs(self, artist_id, count=50, library_ids=None):
        self.similar_songs_called = True
        return list(self.similar_songs.get(artist_id, []))

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

    async def test_similar_songs_are_the_primary_pool(self):
        # getSimilarSongs2 returns a rich, library-resident pool in one call;
        # it should be used directly and make the artist/genre backfill unnecessary.
        nav = FakeNav(
            artist_tracks={"A1": [_track("seed1")]},
            similar_songs={"A1": [_track(f"s{i}", genre="Rock") for i in range(60)]},
        )
        proc = RadioProcessor(nav)
        seed = {"type": "artist", "id": "A1", "name": "Alpha",
                "artist_id": "A1", "artist_name": "Alpha", "genre": None}
        pool = await proc.gather_candidate_tracks(seed)
        ids = [t["id"] for t in pool]

        self.assertTrue(nav.similar_songs_called, "getSimilarSongs2 should be consulted")
        self.assertIn("s0", ids, "similar-songs results should populate the pool")
        self.assertFalse(nav.genre_called, "a rich similar-songs pool needs no genre fallback")
        self.assertEqual(len(ids), len(set(ids)), "pool must be de-duplicated")

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


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeHttp:
    """Captures the last GET and returns a canned Subsonic payload."""

    def __init__(self, payload):
        self._payload = payload
        self.last_url = None
        self.last_params = None

    async def get(self, url, params=None):
        self.last_url = url
        self.last_params = params
        return _FakeResponse(self._payload)


def _make_nav_client(payload):
    """Build a NavidromeClient wired to a fake HTTP layer (no network/env)."""
    client = NavidromeClient.__new__(NavidromeClient)
    client.base_url = "http://nav.local"
    client.api_key = "k"
    client.username = "u"
    client.password = None
    client._auth_token = None
    client._subsonic_token = None
    client._subsonic_salt = None
    client.client = _FakeHttp(payload)
    return client


def _similar_songs_payload(songs, status="ok"):
    body = {"status": status}
    if status != "ok":
        body["error"] = {"message": "boom"}
    else:
        body["similarSongs2"] = {"song": songs}
    return {"subsonic-response": body}


class GetSimilarSongsTests(unittest.IsolatedAsyncioTestCase):

    def _song(self, sid, **over):
        s = {
            "id": sid, "title": "T", "artist": "Artist", "artistId": "A9",
            "album": "Alb", "year": 2011, "genre": "Alt Rock",
            "playCount": 7, "starred": "2020-01-01T00:00:00Z",
        }
        s.update(over)
        return s

    async def test_maps_song_list_to_standard_track_dicts(self):
        nav = _make_nav_client(_similar_songs_payload([self._song("s1"), self._song("s2")]))
        tracks = await nav.get_similar_songs("A9", count=25)

        self.assertEqual([t["id"] for t in tracks], ["s1", "s2"])
        t = tracks[0]
        # shape must match get_tracks_by_artist / get_song so the AI curator is happy
        self.assertEqual(
            set(t),
            {"id", "title", "artist", "artist_id", "album", "year", "genre",
             "play_count", "local_library_likes"},
        )
        self.assertEqual(t["artist_id"], "A9")
        self.assertEqual(t["play_count"], 7)
        self.assertTrue(t["local_library_likes"])  # starred -> liked

        # calls the getSimilarSongs2 endpoint with id + count
        self.assertIn("getSimilarSongs2.view", nav.client.last_url)
        self.assertEqual(nav.client.last_params.get("id"), "A9")
        self.assertEqual(nav.client.last_params.get("count"), 25)

    async def test_single_song_object_is_coerced_to_list(self):
        # Subsonic returns a bare object (not a list) when there's exactly one song
        nav = _make_nav_client(_similar_songs_payload(self._song("only")))
        tracks = await nav.get_similar_songs("A9")
        self.assertEqual([t["id"] for t in tracks], ["only"])

    async def test_empty_when_no_songs(self):
        nav = _make_nav_client(_similar_songs_payload([]))
        self.assertEqual(await nav.get_similar_songs("A9"), [])

    async def test_empty_on_api_error_status(self):
        nav = _make_nav_client(_similar_songs_payload(None, status="failed"))
        self.assertEqual(await nav.get_similar_songs("A9"), [])


if __name__ == "__main__":
    unittest.main()
