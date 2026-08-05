"""Tests for the Last.fm top-tracks fallback in Re-Discover Weekly v2.

Re-Discover reads Navidrome's own `played` timestamps, so a listener who plays
through another app (scrobbling to Last.fm) looks idle and hits the fallback.
These tests cover resolving ranked Last.fm top tracks to the local library and
the fallback's build/skip decisions — with fakes, no live API.

Run from the repo root:
    python -m unittest tests.test_rediscover_lastfm
"""

import unittest

from backend.rediscover import ReDiscoverV2Processor


def _song(sid, artist, title):
    return {"id": sid, "artist": artist, "title": title, "album": "Alb", "year": 2001}


class FakeSearchNav:
    """Returns the whole fake library for any search; resolution filters by key."""

    def __init__(self, library):
        self.library = library

    async def search_songs(self, query, count=20, library_ids=None):
        return list(self.library)


class FakeLastfm:
    def __init__(self, top, user_enabled=True):
        self._top = top
        self.user_enabled = user_enabled

    async def top_tracks(self, period="6month", limit=200):
        return list(self._top)


def _processor(nav=None, lastfm=None, track_count=25, min_tracks=10):
    """Build a v2 processor without running __init__ (which sets no network state)."""
    proc = ReDiscoverV2Processor.__new__(ReDiscoverV2Processor)
    proc.navidrome_client = nav
    proc.lastfm_client = lastfm
    proc.config = {"track_count": track_count, "min_target_period_tracks": min_tracks}
    return proc


class ResolveLastfmTracksTests(unittest.IsolatedAsyncioTestCase):

    async def test_matches_by_folded_key_and_skips_unowned(self):
        library = [_song("s1", "The Beatles", "Hey Jude"), _song("s2", "Oasis", "Wonderwall")]
        proc = _processor(nav=FakeSearchNav(library))
        top = [
            {"artist": "beatles", "title": "hey jude"},   # folds to s1
            {"artist": "Oasis", "title": "Wonderwall"},    # exact s2
            {"artist": "Someone", "title": "Not Owned"},   # no match → skipped
        ]
        resolved = await proc._resolve_lastfm_tracks(top, None, needed=5)
        self.assertEqual([t["id"] for t in resolved], ["s1", "s2"])

    async def test_dedupes_and_caps_at_needed(self):
        library = [_song("s1", "A", "One"), _song("s2", "B", "Two")]
        proc = _processor(nav=FakeSearchNav(library))
        top = [
            {"artist": "A", "title": "One"},
            {"artist": "A", "title": "One"},  # duplicate row → not double-counted
            {"artist": "B", "title": "Two"},
        ]
        resolved = await proc._resolve_lastfm_tracks(top, None, needed=1)
        self.assertEqual([t["id"] for t in resolved], ["s1"])  # capped at needed


class LastfmFallbackTests(unittest.IsolatedAsyncioTestCase):

    async def test_none_without_lastfm_client(self):
        proc = _processor(nav=FakeSearchNav([]), lastfm=None)
        self.assertIsNone(await proc._lastfm_top_tracks_fallback("u", "s", None))

    async def test_none_when_too_few_resolve(self):
        library = [_song("s1", "A", "One")]
        lastfm = FakeLastfm([{"artist": "A", "title": "One"}])
        proc = _processor(nav=FakeSearchNav(library), lastfm=lastfm, min_tracks=10)
        self.assertIsNone(await proc._lastfm_top_tracks_fallback("u", "s", None))

    async def test_builds_playlist_when_enough_resolve(self):
        library = [_song("s1", "A", "One"), _song("s2", "B", "Two"), _song("s3", "C", "Three")]
        top = [{"artist": "A", "title": "One"}, {"artist": "B", "title": "Two"}, {"artist": "C", "title": "Three"}]
        proc = _processor(nav=FakeSearchNav(library), lastfm=FakeLastfm(top), min_tracks=2)
        result = await proc._lastfm_top_tracks_fallback("u", "s", None)
        self.assertIsNotNone(result)
        self.assertEqual(result["mode"], "LASTFM_FALLBACK")
        self.assertTrue(result["is_fallback"])
        self.assertEqual([t["id"] for t in result["tracks"]], ["s1", "s2", "s3"])
        self.assertFalse(result["tracks"][0]["ai_curated"])


if __name__ == "__main__":
    unittest.main()
