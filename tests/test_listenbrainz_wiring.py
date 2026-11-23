"""Wiring and boundary tests for the ListenBrainz source (test-stage findings, #65).

These pin behaviour the first test pass left unproven: Re-Discover's fallback
ordering and parameters, Radio's default suggestion count and seed-id choice,
`mbid` propagation on every Navidrome track path, and the client/health request
details (algorithm, LISTENBRAINZ_URL, cache expiry).

    python -m pytest tests/test_listenbrainz_wiring.py
"""

import asyncio
import os
import unittest
from unittest import mock

import httpx

from backend import listenbrainz_client as lbc
from backend.listenbrainz_client import ListenBrainzClient
from backend.radio import RadioProcessor
from backend.rediscover import ReDiscoverV2Processor
from backend.services import health_check_service as hcs
from backend.services.health_check_service import HealthCheckService
from tests.test_listenbrainz_client import FakeHttp, FakeResponse, SIMILAR, FEEDBACK, make, run
from tests.test_radio import FakeNav, _FakeResponse, _make_nav_client


# ---------------------------------------------------------------- Re-Discover

def _song(sid, artist, title):
    return {"id": sid, "artist": artist, "title": title, "album": "Alb", "year": 2001}


class _Nav:
    """Search returns the whole library; starred is tracked so fall-through is visible."""

    def __init__(self, library, starred=None):
        self.library, self.starred, self.starred_called = library, starred or [], False

    async def search_songs(self, query, count=20, library_ids=None):
        return list(self.library)

    async def get_starred(self, library_ids=None):
        self.starred_called = True
        return list(self.starred)


class _LB:
    def __init__(self, top):
        self._top, self.user_enabled, self.calls = top, True, []

    async def top_tracks(self, range_="half_yearly", limit=200):
        self.calls.append((range_, limit))
        return list(self._top)


def _proc(nav, lb, min_tracks=2, track_count=25):
    p = ReDiscoverV2Processor.__new__(ReDiscoverV2Processor)
    p.navidrome_client, p.listenbrainz_client = nav, lb
    p.config = {"track_count": track_count, "min_target_period_tracks": min_tracks,
                "exclude_played_within_days": 30}
    return p


class RediscoverFallbackWiringTests(unittest.IsolatedAsyncioTestCase):

    @staticmethod
    async def _fall_through(nav, lb, **kw):
        # With nothing usable anywhere the whole chain ends in its final error;
        # what matters here is that the starred step was reached.
        try:
            await _proc(nav, lb, **kw)._trigger_fallback("u", "s")
        except Exception:
            pass

    async def test_trigger_fallback_uses_listenbrainz_first(self):
        lib = [_song("s1", "A", "One"), _song("s2", "B", "Two")]
        lb = _LB([{"artist": "A", "title": "One"}, {"artist": "B", "title": "Two"}])
        nav = _Nav(lib, starred=[_song("x", "Z", "Z")])
        result = await _proc(nav, lb)._trigger_fallback("u", "s")
        self.assertEqual(result["mode"], "LISTENING_HISTORY_FALLBACK")
        self.assertFalse(nav.starred_called)  # ListenBrainz won; starred never consulted

    async def test_trigger_fallback_requests_six_months_up_to_200(self):
        lb = _LB([{"artist": "A", "title": "One"}])
        await _proc(_Nav([_song("s1", "A", "One")]), lb, min_tracks=1)._trigger_fallback("u", "s")
        self.assertEqual(lb.calls, [("half_yearly", 200)])

    async def test_stats_not_ready_falls_through_to_starred(self):
        nav = _Nav([], starred=[])
        await self._fall_through(nav, _LB([]))  # 204 -> []
        self.assertTrue(nav.starred_called)

    async def test_too_few_resolved_falls_through_to_starred(self):
        nav = _Nav([_song("s1", "A", "One")])
        await self._fall_through(nav, _LB([{"artist": "A", "title": "One"}]), min_tracks=5)
        self.assertTrue(nav.starred_called)

    async def test_exactly_the_minimum_resolved_is_enough(self):
        lib = [_song("s1", "A", "One"), _song("s2", "B", "Two")]
        lb = _LB([{"artist": "A", "title": "One"}, {"artist": "B", "title": "Two"}])
        result = await _proc(_Nav(lib), lb, min_tracks=2)._listening_history_fallback("u", "s")
        self.assertIsNotNone(result)  # "at least the minimum" includes equality


# ---------------------------------------------------------------------- Radio

class _LBSimilar:
    enabled = True

    def __init__(self, names):
        self.names = names

    async def similar_artists(self, mbid, limit=40):
        return [{"name": n, "match": "1"} for n in self.names]


class RadioSuggestionTests(unittest.IsolatedAsyncioTestCase):

    async def test_default_is_the_top_eight(self):
        nav = FakeNav(artists=[{"id": "A1", "name": "Alpha"}], mbids={"A1": "m"})
        proc = RadioProcessor(nav, _LBSimilar([f"Artist {i}" for i in range(12)]))
        seed = {"type": "artist", "id": "A1", "artist_id": "A1", "name": "Alpha"}
        result = await proc.similar_out_of_library_artists(seed)
        self.assertEqual([r["name"] for r in result], [f"Artist {i}" for i in range(8)])

    async def test_mbid_is_looked_up_by_artist_id_not_song_id(self):
        nav = FakeNav(artists=[], mbids={"ARTIST": "right", "SONG": "wrong"})
        seen = []
        lb = _LBSimilar(["X"])

        async def spy(mbid, limit=40):
            seen.append(mbid)
            return [{"name": "X", "match": "1"}]
        lb.similar_artists = spy
        seed = {"type": "song", "id": "SONG", "artist_id": "ARTIST", "name": "t"}
        await RadioProcessor(nav, lb).similar_out_of_library_artists(seed)
        self.assertEqual(seen, ["right"])


# ------------------------------------------------- Navidrome mbid propagation

class _Router:
    """Fake HTTP layer answering by Subsonic endpoint name."""

    def __init__(self, routes):
        self.routes = routes

    async def get(self, url, params=None):
        for key, payload in self.routes.items():
            if key in url:
                return _FakeResponse({"subsonic-response": {"status": "ok", **payload}})
        raise AssertionError(f"unexpected url {url}")


def _nav(routes):
    client = _make_nav_client({})
    client.client = _Router(routes)
    return client


SONG = {"id": "s1", "title": "T", "artist": "Art", "musicBrainzId": "rec-1", "artistId": "A1"}


class NavidromeMbidTests(unittest.IsolatedAsyncioTestCase):

    async def test_get_tracks_by_artist_carries_mbid(self):
        nav = _nav({"getArtist": {"artist": {"name": "Art", "album": [{"id": "al1", "name": "Alb"}]}},
                    "getAlbum": {"album": {"song": [SONG]}}})
        self.assertEqual((await nav.get_tracks_by_artist("A1"))[0]["mbid"], "rec-1")

    async def test_get_tracks_by_genre_carries_mbid(self):
        nav = _nav({"getSongsByGenre": {"songsByGenre": {"song": [SONG]}}})
        nav.client.routes = {"getSongsByGenre": {"songsByGenre": {"song": [SONG]}}}
        # one page then an empty page ends the loop
        pages = [{"songsByGenre": {"song": [SONG]}}, {"songsByGenre": {"song": []}}]

        class Paged:
            async def get(self, url, params=None):
                return _FakeResponse({"subsonic-response": {"status": "ok", **pages.pop(0)}})
        nav.client = Paged()
        self.assertEqual((await nav.get_tracks_by_genre("Rock"))[0]["mbid"], "rec-1")

    async def test_get_starred_carries_mbid(self):
        nav = _nav({"getStarred": {"starred": {"song": [SONG]}}})
        self.assertEqual((await nav.get_starred())[0]["mbid"], "rec-1")

    async def test_get_song_carries_mbid(self):
        nav = _nav({"getSong": {"song": SONG}})
        self.assertEqual((await nav.get_song("s1"))["mbid"], "rec-1")

    async def test_search_songs_carries_mbid(self):
        nav = _nav({"search3": {"searchResult3": {"song": [SONG]}}})
        self.assertEqual((await nav.search_songs("q"))[0]["mbid"], "rec-1")


# --------------------------------------------------------------------- Client

class ClientRequestTests(unittest.TestCase):

    def test_labs_request_names_the_algorithm(self):
        http = FakeHttp(FakeResponse(SIMILAR))
        run(make(http=http).similar_artists("seed"))
        self.assertEqual(http.calls[0][1]["algorithm"], lbc.SIMILAR_ALGORITHM)

    def test_loved_cache_expires_after_the_ttl(self):
        http = FakeHttp(FakeResponse(FEEDBACK))
        c = make(http=http)
        with mock.patch.object(lbc.time, "monotonic", return_value=1000.0):
            run(c.loved_tracks())
            run(c.loved_tracks())
            self.assertEqual(len(http.calls), 1)
        with mock.patch.object(lbc.time, "monotonic", return_value=1000.0 + lbc.LOVED_CACHE_TTL_SECONDS + 1):
            run(c.loved_tracks())
        self.assertEqual(len(http.calls), 2)

    def test_requests_go_through_listenbrainz_url(self):
        os.environ["LISTENBRAINZ_URL"] = "https://lb.example/"
        try:
            c = ListenBrainzClient()
        finally:
            del os.environ["LISTENBRAINZ_URL"]
        c.username, c.client = "u", FakeHttp(FakeResponse({"payload": {"recordings": []}}))
        run(c.top_tracks())
        self.assertTrue(c.client.calls[0][0].startswith("https://lb.example/1/stats/"))


# --------------------------------------------------------------------- Health

class _HC:
    calls = []

    def __init__(self, status=200, payload=None, raises=None):
        type(self).status, type(self).payload, type(self).raises = status, payload, raises

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **k):
        type(self).calls.append(url)
        if self.raises:
            raise self.raises
        return mock.Mock(status_code=self.status, json=lambda: self.payload,
                         raise_for_status=lambda: (_ for _ in ()).throw(
                             httpx.HTTPStatusError("x", request=None, response=None))
                         if self.status >= 400 else None)


class HealthDetailTests(unittest.IsolatedAsyncioTestCase):

    async def _run(self, env, client):
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(hcs.httpx, "AsyncClient", client):
            return await HealthCheckService()._check_listenbrainz()

    async def test_honours_listenbrainz_url(self):
        _HC.calls = []
        await self._run({"LISTENBRAINZ_USERNAME": "me", "LISTENBRAINZ_URL": "https://lb.example/"},
                        _HC(payload={"payload": {"count": 1}}))
        self.assertEqual(_HC.calls, ["https://lb.example/1/user/me/listen-count"])

    async def test_server_error_is_a_warning(self):
        r = await self._run({"LISTENBRAINZ_USERNAME": "me"}, _HC(status=500))
        self.assertEqual(r["status"], "warning")

    async def test_unreadable_count_is_still_reachable(self):
        r = await self._run({"LISTENBRAINZ_USERNAME": "me"}, _HC(payload={"x": 1}))
        self.assertEqual(r["status"], "success")
        self.assertNotIn("listens", r["message"])


if __name__ == "__main__":
    unittest.main()
