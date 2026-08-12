"""Tests for the Radio guarantees enforced after curation, and for what happens
when the candidate pool degrades.

tests/test_radio.py covers the happy paths — pooling, dedup, seed resolution,
seed-first promotion. This covers the two things that only show up when
something goes wrong: the per-artist cap that stops a thin pool producing a
one-artist station, and the warnings that tell a listener their station was
built from a fallback.

Run from the repo root:
    python -m unittest tests.test_radio_guarantees
    python -m pytest tests/test_radio_guarantees.py
"""

import unittest

from backend.radio import (
    RadioProcessor,
    artist_key,
    artists_needed,
    count_distinct_artists,
    enforce_artist_cap,
)


def _track(tid, artist="A", artist_id=None):
    track = {"id": tid, "title": f"T{tid}", "artist": artist}
    if artist_id:
        track["artist_id"] = artist_id
    return track


class ExplodingNav:
    """A Navidrome stand-in where each call can be made to fail on demand.

    Every step of pool-building is allowed to fail independently, so each one
    needs its own failure injected to prove it degrades rather than raises.
    """

    def __init__(self, fail=(), artist_tracks=None, similar=None,
                 genre_tracks=None, artists=None, similar_songs=None):
        self.fail = set(fail)
        self.artist_tracks = artist_tracks or {}
        self.similar = similar or []
        self.genre_tracks = genre_tracks or {}
        self.artists = artists or []
        self.similar_songs = similar_songs or {}

    def _maybe_fail(self, name):
        if name in self.fail:
            raise Exception(f"{name} exploded")

    async def get_similar_songs(self, artist_id, count=50, library_ids=None):
        self._maybe_fail("similar_songs")
        return list(self.similar_songs.get(artist_id, []))

    async def get_tracks_by_artist(self, artist_id, library_ids=None):
        self._maybe_fail("artist_tracks")
        return list(self.artist_tracks.get(artist_id, []))

    async def get_similar_artists(self, artist_id, count=20):
        self._maybe_fail("similar_artists")
        return list(self.similar)

    async def get_tracks_by_genre(self, genre, library_ids=None):
        self._maybe_fail("genre")
        return list(self.genre_tracks.get(genre, []))

    async def get_artists(self, library_ids=None):
        self._maybe_fail("artists")
        return list(self.artists)


class FailingLastfm:
    enabled = True

    async def similar_artists(self, artist_name, limit=40):
        raise Exception("last.fm is down")


class StubLastfm:
    def __init__(self, similar, enabled=True):
        self.enabled = enabled
        self._similar = similar

    async def similar_artists(self, artist_name, limit=40):
        return list(self._similar)


class ArtistKeyTests(unittest.TestCase):
    def test_prefers_the_stable_id(self):
        self.assertEqual(artist_key({"artist_id": "A1", "artist": "Beta"}), "A1")

    def test_accepts_the_camel_case_spelling(self):
        # Navidrome payloads arrive in both shapes depending on the endpoint.
        self.assertEqual(artist_key({"artistId": "A1"}), "A1")

    def test_falls_back_to_the_lowercased_name(self):
        # Genre-fallback tracks often arrive with no artist id at all.
        self.assertEqual(artist_key({"artist": "Beta"}), "beta")

    def test_a_track_with_no_artist_at_all_is_grouped_as_unknown(self):
        self.assertEqual(artist_key({}), "unknown")


class DistinctArtistTests(unittest.TestCase):
    def test_counts_unique_artists(self):
        tracks = [_track("1", artist_id="A"), _track("2", artist_id="A"), _track("3", artist_id="B")]
        self.assertEqual(count_distinct_artists(tracks), 2)

    def test_an_empty_list_has_no_artists(self):
        self.assertEqual(count_distinct_artists([]), 0)

    def test_artists_needed_rounds_up(self):
        # 25 tracks at 2 per artist needs 13 artists, not 12.5
        self.assertEqual(artists_needed(25, 2), 13)
        self.assertEqual(artists_needed(10, 5), 2)

    def test_a_zero_cap_is_treated_as_one(self):
        # Guards a divide-by-zero rather than expressing a real policy.
        self.assertEqual(artists_needed(4, 0), 4)


class ArtistCapTests(unittest.TestCase):
    """The cap is what stops a deep-catalogue artist owning the whole station."""

    def test_tracks_beyond_the_cap_are_dropped(self):
        tracks = [_track(str(i), artist_id="A") for i in range(5)]
        kept, meta = enforce_artist_cap(tracks, max_per_artist=2)
        self.assertEqual(len(kept), 2)
        self.assertEqual(meta["dropped_for_cap"], 3)

    def test_curation_order_is_preserved(self):
        tracks = [
            _track("a1", artist_id="A"), _track("b1", artist_id="B"),
            _track("a2", artist_id="A"), _track("a3", artist_id="A"),
            _track("c1", artist_id="C"),
        ]
        kept, _ = enforce_artist_cap(tracks, max_per_artist=2)
        self.assertEqual([t["id"] for t in kept], ["a1", "b1", "a2", "c1"])

    def test_a_thin_pool_yields_a_short_list_rather_than_a_padded_one(self):
        # The documented trade-off: 10 tracks by one artist and a cap of 2 gives
        # 2 tracks, never 10 filled out with repeats.
        tracks = [_track(str(i), artist_id="A") for i in range(10)]
        kept, _ = enforce_artist_cap(tracks, max_per_artist=2, num_tracks=10)
        self.assertEqual(len(kept), 2)

    def test_it_stops_once_the_requested_length_is_reached(self):
        tracks = [_track(str(i), artist_id=f"A{i}") for i in range(10)]
        kept, _ = enforce_artist_cap(tracks, max_per_artist=2, num_tracks=4)
        self.assertEqual(len(kept), 4)

    def test_metadata_reports_what_happened(self):
        tracks = [_track("1", artist_id="A"), _track("2", artist_id="A"), _track("3", artist_id="B")]
        _, meta = enforce_artist_cap(tracks, max_per_artist=1)
        self.assertEqual(meta, {"dropped_for_cap": 1, "distinct_artists": 2, "max_per_artist": 1})

    def test_an_empty_curation_is_handled(self):
        kept, meta = enforce_artist_cap([], max_per_artist=2)
        self.assertEqual(kept, [])
        self.assertEqual(meta["distinct_artists"], 0)


class PoolDegradationTests(unittest.IsolatedAsyncioTestCase):
    """Every pool step may fail; none may raise, and all must be reported."""

    SEED = {"type": "artist", "id": "A1", "name": "Alpha", "artist_id": "A1", "artist_name": "Alpha"}

    async def test_a_failed_similar_songs_lookup_warns_about_a_reduced_pool(self):
        nav = ExplodingNav(fail=["similar_songs"], artist_tracks={"A1": [_track("t1", artist_id="A1")]})
        processor = RadioProcessor(nav)

        tracks = await processor.gather_candidate_tracks(self.SEED)

        self.assertEqual([t["id"] for t in tracks], ["t1"])
        self.assertEqual(len(processor.pool_warnings), 1)
        # The listener's real question is "why does this look like last time?"
        self.assertIn("much like the last one", processor.pool_warnings[0])

    async def test_a_failed_seed_artist_lookup_is_reported(self):
        nav = ExplodingNav(fail=["artist_tracks"], similar_songs={"A1": [_track("s1")]})
        processor = RadioProcessor(nav)

        await processor.gather_candidate_tracks(self.SEED)

        self.assertTrue(any("seed artist's tracks" in w for w in processor.pool_warnings))

    async def test_a_failed_similar_artist_lookup_is_reported(self):
        nav = ExplodingNav(fail=["similar_artists"])
        processor = RadioProcessor(nav)

        await processor.gather_candidate_tracks(self.SEED)

        self.assertTrue(any("similar artists" in w for w in processor.pool_warnings))

    async def test_a_failed_genre_fallback_is_reported(self):
        seed = {**self.SEED, "genre": "Shoegaze"}
        nav = ExplodingNav(fail=["genre"])
        processor = RadioProcessor(nav)

        await processor.gather_candidate_tracks(seed)

        self.assertTrue(any("genre fallback" in w for w in processor.pool_warnings))

    async def test_everything_failing_returns_an_empty_pool_not_an_exception(self):
        nav = ExplodingNav(fail=["similar_songs", "artist_tracks", "similar_artists", "genre"])
        processor = RadioProcessor(nav)

        tracks = await processor.gather_candidate_tracks({**self.SEED, "genre": "Doom"})

        self.assertEqual(tracks, [])
        self.assertGreaterEqual(len(processor.pool_warnings), 3)

    async def test_warnings_reset_between_builds(self):
        nav = ExplodingNav(fail=["similar_songs"], artist_tracks={"A1": [_track("t1")]})
        processor = RadioProcessor(nav)

        await processor.gather_candidate_tracks(self.SEED)
        await processor.gather_candidate_tracks(self.SEED)

        # A rebuild must not inherit the previous build's complaints.
        self.assertEqual(len(processor.pool_warnings), 1)


class AlbumSuggestionSourceTests(unittest.IsolatedAsyncioTestCase):
    """Suggestions are Last.fm's similar artists minus everything already owned."""

    SEED = {"type": "artist", "id": "A1", "name": "Alpha", "artist_id": "A1", "artist_name": "Alpha"}

    async def test_no_lastfm_means_no_grounded_suggestions(self):
        processor = RadioProcessor(ExplodingNav(), lastfm_client=None)
        self.assertEqual(await processor.similar_out_of_library_artists(self.SEED), [])

    async def test_a_disabled_client_is_skipped(self):
        processor = RadioProcessor(ExplodingNav(), StubLastfm([{"name": "Beta"}], enabled=False))
        self.assertEqual(await processor.similar_out_of_library_artists(self.SEED), [])

    async def test_a_seed_with_no_artist_name_is_skipped(self):
        processor = RadioProcessor(ExplodingNav(), StubLastfm([{"name": "Beta"}]))
        self.assertEqual(await processor.similar_out_of_library_artists({"type": "artist"}), [])

    async def test_a_lastfm_failure_degrades_and_warns(self):
        processor = RadioProcessor(ExplodingNav(), FailingLastfm())

        self.assertEqual(await processor.similar_out_of_library_artists(self.SEED), [])
        self.assertTrue(any("album-suggestion" in w for w in processor.pool_warnings))

    async def test_an_empty_similar_list_yields_nothing(self):
        processor = RadioProcessor(ExplodingNav(), StubLastfm([]))
        self.assertEqual(await processor.similar_out_of_library_artists(self.SEED), [])

    async def test_a_failed_library_lookup_gives_up_rather_than_suggesting_owned_artists(self):
        # Without the library list there is no way to tell a gap from something
        # already owned, and suggesting an album the listener has is worse than
        # suggesting nothing.
        nav = ExplodingNav(fail=["artists"])
        processor = RadioProcessor(nav, StubLastfm([{"name": "Beta", "match": "0.9"}]))

        self.assertEqual(await processor.similar_out_of_library_artists(self.SEED), [])

    async def test_the_limit_is_applied_to_the_gaps(self):
        similar = [{"name": f"Band {i}", "match": "0.5"} for i in range(10)]
        processor = RadioProcessor(ExplodingNav(artists=[]), StubLastfm(similar))

        result = await processor.similar_out_of_library_artists(self.SEED, limit=3)

        self.assertEqual([r["name"] for r in result], ["Band 0", "Band 1", "Band 2"])


if __name__ == "__main__":
    unittest.main()
