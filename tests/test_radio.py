"""Lightweight, dependency-free tests for the Radio feature.

Covers the pure logic only — candidate pooling/dedup, seed resolution and the
AI response parsing — using fakes so no live Navidrome server or AI provider is
required.

Run from the repo root:
    python -m unittest tests.test_radio        # stdlib only
    python -m pytest tests/test_radio.py       # if pytest is installed
"""

import unittest

from backend.radio import (
    RadioProcessor, MAX_SIMILAR_ARTISTS, build_shortfall, cap_seed_artist,
    lidarr_add_url, promote_seed_first, seed_artist_limit
)
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


class PromoteSeedFirstTests(unittest.TestCase):
    """The station must open with the seed artist/song, whatever the curator returned."""

    def _artist_seed(self):
        return {"type": "artist", "id": "A1", "name": "Alpha",
                "artist_id": "A1", "artist_name": "Alpha"}

    def _by(self, tid, artist, artist_id=None):
        track = _track(tid, artist=artist)
        track["artist_id"] = artist_id
        return track

    def test_seed_artist_track_moves_to_front(self):
        curated = [
            self._by("x", "Beta", "A2"),
            self._by("y", "Gamma", "A3"),
            self._by("seed", "Alpha", "A1"),
        ]
        result = promote_seed_first(curated, self._artist_seed())
        self.assertEqual([t["id"] for t in result], ["seed", "x", "y"])

    def test_order_untouched_when_seed_already_opens(self):
        curated = [self._by("seed", "Alpha", "A1"), self._by("x", "Beta", "A2")]
        result = promote_seed_first(curated, self._artist_seed())
        self.assertEqual([t["id"] for t in result], ["seed", "x"])

    def test_earliest_seed_track_wins_when_several_present(self):
        curated = [
            self._by("x", "Beta", "A2"),
            self._by("s1", "Alpha", "A1"),
            self._by("s2", "Alpha", "A1"),
        ]
        result = promote_seed_first(curated, self._artist_seed())
        self.assertEqual(result[0]["id"], "s1", "curator ordering breaks the tie")

    def test_seed_song_beats_another_track_by_the_same_artist(self):
        seed = {"type": "song", "id": "S1", "name": "Track — Alpha",
                "artist_id": "A1", "artist_name": "Alpha", "song_title": "Track"}
        curated = [
            self._by("other", "Alpha", "A1"),   # right artist, wrong song
            self._by("S1", "Alpha", "A1"),      # the seed itself
        ]
        result = promote_seed_first(curated, seed)
        self.assertEqual(result[0]["id"], "S1")

    def test_matches_on_name_when_track_has_no_artist_id(self):
        # Genre-fallback tracks can arrive without an artist_id
        curated = [self._by("x", "Beta", "A2"), self._by("seed", "Alpha", None)]
        result = promote_seed_first(curated, self._artist_seed())
        self.assertEqual(result[0]["id"], "seed")

    def test_seed_pulled_from_pool_when_curator_dropped_it(self):
        curated = [self._by("x", "Beta", "A2"), self._by("y", "Gamma", "A3")]
        pool = curated + [self._by("seed", "Alpha", "A1")]
        result = promote_seed_first(curated, self._artist_seed(), pool)
        self.assertEqual(result[0]["id"], "seed")
        self.assertEqual(len(result), len(curated), "length must be preserved")
        self.assertEqual([t["id"] for t in result], ["seed", "x"])

    def test_left_alone_when_seed_is_nowhere_to_be_found(self):
        curated = [self._by("x", "Beta", "A2"), self._by("y", "Gamma", "A3")]
        result = promote_seed_first(curated, self._artist_seed(), curated)
        self.assertEqual([t["id"] for t in result], ["x", "y"])

    def test_empty_curation_is_returned_unchanged(self):
        self.assertEqual(promote_seed_first([], self._artist_seed(), []), [])


class SeedArtistCapTests(unittest.TestCase):
    """The seed artist may occupy at most 20% of the station."""

    def _seed(self):
        return {"type": "artist", "id": "A1", "name": "Alpha",
                "artist_id": "A1", "artist_name": "Alpha"}

    def _by(self, tid, artist, artist_id=None):
        track = _track(tid, artist=artist)
        track["artist_id"] = artist_id
        return track

    def test_limit_is_twenty_percent(self):
        self.assertEqual(seed_artist_limit(25), 5)
        self.assertEqual(seed_artist_limit(50), 10)
        self.assertEqual(seed_artist_limit(100), 20)

    def test_limit_never_drops_below_one(self):
        # promote_seed_first guarantees a seed track, so the cap must allow one
        self.assertEqual(seed_artist_limit(4), 1)
        self.assertEqual(seed_artist_limit(1), 1)

    def test_limit_floors_rather_than_rounds_up(self):
        # 9 * 0.2 = 1.8 -> 1, so the share is a true ceiling
        self.assertEqual(seed_artist_limit(9), 1)

    def test_excess_seed_tracks_are_replaced_from_the_pool(self):
        curated = [self._by(f"s{i}", "Alpha", "A1") for i in range(10)]
        pool = curated + [self._by(f"o{i}", f"Other{i}", f"A{i + 2}") for i in range(20)]

        kept, stats = cap_seed_artist(curated, self._seed(), num_tracks=10, candidate_pool=pool)

        seed_tracks = [t for t in kept if t["artist"] == "Alpha"]
        self.assertEqual(len(seed_tracks), 2, "10 tracks -> 20% -> 2 seed tracks")
        self.assertEqual(len(kept), 10, "gap is backfilled, not left short")
        self.assertEqual(stats["dropped_for_seed_cap"], 8)
        self.assertEqual(stats["backfilled"], 8)

    def test_earliest_seed_tracks_are_the_ones_kept(self):
        curated = [self._by(f"s{i}", "Alpha", "A1") for i in range(5)]
        kept, _ = cap_seed_artist(curated, self._seed(), num_tracks=10, candidate_pool=curated)
        self.assertEqual([t["id"] for t in kept], ["s0", "s1"], "curation order is respected")

    def test_station_comes_up_short_when_pool_has_nobody_else(self):
        # A thin library: the seed artist is all there is. Better a short station
        # than a greatest-hits — build_shortfall then reports the gap.
        curated = [self._by(f"s{i}", "Alpha", "A1") for i in range(10)]
        kept, stats = cap_seed_artist(curated, self._seed(), num_tracks=10, candidate_pool=curated)
        self.assertEqual(len(kept), 2)
        self.assertEqual(stats["backfilled"], 0)

    def test_other_artists_are_untouched(self):
        curated = [self._by(f"o{i}", f"Other{i}", f"A{i + 2}") for i in range(10)]
        kept, stats = cap_seed_artist(curated, self._seed(), num_tracks=10, candidate_pool=curated)
        self.assertEqual(len(kept), 10)
        self.assertEqual(stats["dropped_for_seed_cap"], 0)
        self.assertEqual(stats["seed_artist_tracks"], 0)

    def test_backfill_never_reintroduces_the_seed_artist(self):
        curated = [self._by(f"s{i}", "Alpha", "A1") for i in range(6)]
        # Pool is mostly seed tracks with a couple of others
        pool = curated + [self._by("o1", "Other", "A9"), self._by("o2", "Other2", "A8")]
        kept, _ = cap_seed_artist(curated, self._seed(), num_tracks=6, candidate_pool=pool)
        seed_tracks = [t for t in kept if t["artist"] == "Alpha"]
        self.assertEqual(len(seed_tracks), 1, "6 * 0.2 = 1.2 -> 1")
        self.assertEqual(len(kept), 3, "only two non-seed tracks existed to backfill with")

    def test_song_seed_caps_the_songs_artist(self):
        seed = {"type": "song", "id": "S1", "name": "Track — Alpha",
                "artist_id": "A1", "artist_name": "Alpha", "song_title": "Track"}
        curated = [self._by(f"s{i}", "Alpha", "A1") for i in range(10)]
        pool = curated + [self._by(f"o{i}", f"Other{i}", f"A{i + 2}") for i in range(20)]
        kept, _ = cap_seed_artist(curated, seed, num_tracks=10, candidate_pool=pool)
        self.assertEqual(len([t for t in kept if t["artist"] == "Alpha"]), 2)

    def test_cap_then_promote_still_opens_with_the_seed(self):
        # The two rules compose: capping keeps the earliest seed track, which
        # promote_seed_first then moves to the front.
        curated = (
            [self._by(f"o{i}", f"Other{i}", f"A{i + 2}") for i in range(8)]
            + [self._by(f"s{i}", "Alpha", "A1") for i in range(4)]
        )
        pool = curated + [self._by(f"x{i}", f"Extra{i}", f"B{i}") for i in range(10)]

        kept, _ = cap_seed_artist(curated, self._seed(), num_tracks=10, candidate_pool=pool)
        kept = promote_seed_first(kept, self._seed(), pool)

        self.assertEqual(kept[0]["artist"], "Alpha", "station still opens with the seed")
        self.assertEqual(len([t for t in kept if t["artist"] == "Alpha"]), 2)


class ShortfallTests(unittest.TestCase):
    """A thin library must be reported, not silently swallowed."""

    def test_full_station_is_not_short(self):
        report = build_shortfall(requested=25, delivered=25, candidate_pool_size=300)
        self.assertFalse(report["is_short"])
        self.assertEqual(report["missing"], 0)
        self.assertEqual(report["message"], "")

    def test_short_station_reports_the_gap(self):
        report = build_shortfall(requested=25, delivered=12, candidate_pool_size=300,
                                 distinct_artists=6)
        self.assertTrue(report["is_short"])
        self.assertEqual(report["missing"], 13)
        self.assertEqual(report["delivered"], 12)
        self.assertEqual(report["requested"], 25)
        self.assertEqual(report["distinct_artists"], 6)
        self.assertIn("12 of the 25", report["message"])

    def test_thin_pool_is_called_out_in_the_message(self):
        report = build_shortfall(requested=25, delivered=8, candidate_pool_size=8)
        self.assertIn("8 candidate tracks", report["message"])

    def test_empty_station_has_its_own_wording(self):
        report = build_shortfall(requested=25, delivered=0, candidate_pool_size=0)
        self.assertTrue(report["is_short"])
        self.assertEqual(report["missing"], 25)
        self.assertIn("didn't have anything similar enough", report["message"])

    def test_over_delivery_is_not_negative(self):
        report = build_shortfall(requested=10, delivered=12, candidate_pool_size=50)
        self.assertFalse(report["is_short"])
        self.assertEqual(report["missing"], 0)


class LidarrUrlTests(unittest.TestCase):
    """Album suggestions deep-link into Lidarr's /add/search?term= page."""

    def test_builds_add_search_url_with_artist_and_album(self):
        url = lidarr_add_url("Bon Iver", "For Emma, Forever Ago",
                             base_url="https://lidarr.example.com")
        self.assertEqual(
            url,
            "https://lidarr.example.com/add/search?term=Bon+Iver+For+Emma%2C+Forever+Ago"
        )

    def test_trailing_slash_does_not_double_up(self):
        url = lidarr_add_url("Alpha", "Beta", base_url="https://lidarr.example.com/")
        self.assertEqual(url, "https://lidarr.example.com/add/search?term=Alpha+Beta")

    def test_artist_only_is_still_a_valid_search(self):
        url = lidarr_add_url("Alpha", None, base_url="https://lidarr.example.com")
        self.assertEqual(url, "https://lidarr.example.com/add/search?term=Alpha")

    def test_no_lidarr_configured_yields_no_link(self):
        self.assertIsNone(lidarr_add_url("Alpha", "Beta", base_url=""))
        self.assertIsNone(lidarr_add_url("Alpha", "Beta", base_url="   "))

    def test_blank_artist_and_album_yields_no_link(self):
        self.assertIsNone(lidarr_add_url("", "", base_url="https://lidarr.example.com"))

    def test_reads_lidarr_url_from_environment(self):
        import os
        previous = os.environ.get("LIDARR_URL")
        os.environ["LIDARR_URL"] = "https://lidarr.example.com"
        try:
            self.assertEqual(
                lidarr_add_url("Alpha", "Beta"),
                "https://lidarr.example.com/add/search?term=Alpha+Beta"
            )
        finally:
            if previous is None:
                del os.environ["LIDARR_URL"]
            else:
                os.environ["LIDARR_URL"] = previous


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


class CurateThisIsFallbackTests(unittest.IsolatedAsyncioTestCase):
    """Regression guard: the 'This Is' curator's error paths must fall back, not crash.

    The exception/parse handlers previously referenced an undefined `candidate_tracks`
    (copy-pasted from the rediscover curator), so any AI failure raised NameError
    instead of returning the play-count fallback. Surfaced by the CI ruff F821 gate.
    """

    async def test_malformed_response_falls_back_by_play_count(self):
        tracks = [
            _track("a", play_count=1),
            _track("b", play_count=9),
            _track("c", play_count=4),
        ]
        client = _make_ai_client(FakeProvider("not json at all"))
        # Before the fix this raised NameError (undefined `candidate_tracks`);
        # now it must return a fallback selection from the provided tracks.
        result = await client.curate_this_is(
            artist_name="Alpha", tracks_json=tracks, num_tracks=2
        )
        self.assertEqual(len(result), 2)
        self.assertTrue(set(result).issubset({"a", "b", "c"}))


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
