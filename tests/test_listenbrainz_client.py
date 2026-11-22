"""Tests for the ListenBrainz client: parsers and the degrade-to-empty request layer.

Fixtures follow the shapes confirmed by the 2026-11-22 probe (issue #65): loved-track
feedback and Labs similar artists. The top-recordings 200 body follows the documented
format (the probe account only produced a 204), so its tests are marked as such.
Track/artist names are synthetic.

No network: the httpx client is replaced by a stub.

    python -m pytest tests/test_listenbrainz_client.py
"""

import asyncio
import unittest

import httpx

from backend.listenbrainz_client import (
    ListenBrainzClient,
    parse_loved_tracks,
    parse_similar_artists,
    parse_top_tracks,
)

FEEDBACK = {
    "count": 3,
    "total_count": 3,
    "offset": 0,
    "feedback": [
        {
            "recording_mbid": "rec-1", "score": 1, "created": 1290000000,
            "track_metadata": {
                "artist_name": "Test Artist", "track_name": "First Song", "release_name": "LP",
                "mbid_mapping": {
                    "recording_mbid": "rec-1", "release_mbid": "rel-1",
                    "artist_mbids": ["art-1"],
                    "artists": [{"artist_mbid": "art-1", "artist_credit_name": "Test Artist"}],
                },
            },
        },
        {   # no mbid_mapping at all: still usable by name
            "recording_mbid": "rec-2", "score": 1,
            "track_metadata": {"artist_name": "Other Band", "track_name": "Second Song"},
        },
        {   # nothing identifying: skipped
            "score": 1, "track_metadata": {},
        },
    ],
}

TOP = {"payload": {"recordings": [
    {"track_name": "Hit", "artist_name": "Test Artist", "recording_mbid": "rec-9", "listen_count": 42},
    {"track_name": "", "artist_name": "Nameless"},
    {"track_name": "Deep Cut", "artist_name": "Other Band", "listen_count": 7},
]}}

SIMILAR = [
    {"artist_mbid": "seed", "name": "Seed", "score": 99999, "reference_mbid": "seed"},
    {"artist_mbid": "a", "name": "Alpha", "score": 9000, "reference_mbid": "seed", "type": "Group"},
    {"artist_mbid": "b", "name": "Beta", "score": 12000, "reference_mbid": "seed"},
    {"artist_mbid": "c", "name": "  ", "score": 11000, "reference_mbid": "seed"},
]


class ParseTests(unittest.TestCase):
    def test_loved_rows_and_mbids(self):
        rows = parse_loved_tracks(FEEDBACK)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"artist": "Test Artist", "title": "First Song", "mbid": "rec-1", "artist_mbid": "art-1"})
        self.assertEqual(rows[1]["artist_mbid"], "")  # entry without mapping

    def test_loved_malformed(self):
        for bad in (None, [], {}, {"feedback": "x"}, {"feedback": [None, 3]}):
            self.assertEqual(parse_loved_tracks(bad), [])

    def test_top_tracks_ranked_and_filtered(self):
        rows = parse_top_tracks(TOP)
        self.assertEqual([r["title"] for r in rows], ["Hit", "Deep Cut"])
        self.assertEqual(rows[0]["playcount"], "42")
        self.assertEqual(rows[0]["mbid"], "rec-9")

    def test_top_tracks_malformed(self):
        for bad in (None, {}, {"payload": {}}, {"payload": {"recordings": None}}, []):
            self.assertEqual(parse_top_tracks(bad), [])

    def test_similar_ranked_by_score_and_drops_seed_and_nameless(self):
        rows = parse_similar_artists(SIMILAR)
        self.assertEqual([r["name"] for r in rows], ["Beta", "Alpha"])
        self.assertEqual(rows[0], {"name": "Beta", "mbid": "b", "match": "12000"})

    def test_similar_malformed(self):
        for bad in (None, {}, "x", [None, 1]):
            self.assertEqual(parse_similar_artists(bad), [])


class FakeResponse:
    def __init__(self, payload=None, status=200, bad_json=False):
        self._payload, self.status_code, self._bad = payload, status, bad_json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._payload


class FakeHttp:
    def __init__(self, *responses, raises=None):
        self.responses = list(responses)
        self.raises = raises
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append((url, params or {}))
        if self.raises:
            raise self.raises
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def make(username="listener", http=None):
    c = ListenBrainzClient()
    c.username = username
    c._loved_cache = None
    if http is not None:
        c.client = http
    return c


def run(coro):
    return asyncio.run(coro)


class ClientTests(unittest.TestCase):
    def test_unconfigured_makes_no_calls(self):
        http = FakeHttp(FakeResponse(FEEDBACK))
        c = make(username="", http=http)
        self.assertFalse(c.enabled)
        self.assertEqual(run(c.loved_tracks()), [])
        self.assertEqual(run(c.top_tracks()), [])
        self.assertEqual(run(c.similar_artists("seed")), [])
        self.assertIsNone(run(c.listen_count()))
        self.assertEqual(http.calls, [])

    def test_loved_tracks_request_and_cache(self):
        http = FakeHttp(FakeResponse(FEEDBACK))
        c = make(http=http)
        self.assertEqual(len(run(c.loved_tracks())), 2)
        self.assertEqual(len(run(c.loved_tracks())), 2)
        self.assertEqual(len(http.calls), 1)  # second answered from cache
        url, params = http.calls[0]
        self.assertTrue(url.endswith("/1/feedback/user/listener/get-feedback"))
        self.assertEqual(params["score"], 1)
        self.assertEqual(params["metadata"], "true")

    def test_loved_tracks_pages_until_total(self):
        full = {"total_count": 150, "feedback": [
            {"recording_mbid": f"r{i}", "track_metadata": {"artist_name": "A", "track_name": f"T{i}"}}
            for i in range(100)]}
        rest = {"total_count": 150, "feedback": [
            {"recording_mbid": f"s{i}", "track_metadata": {"artist_name": "A", "track_name": f"U{i}"}}
            for i in range(50)]}
        http = FakeHttp(FakeResponse(full), FakeResponse(rest))
        rows = run(make(http=http).loved_tracks())
        self.assertEqual(len(rows), 150)
        self.assertEqual([c[1]["offset"] for c in http.calls], [0, 100])

    def test_failures_degrade_and_are_not_cached(self):
        cases = [
            FakeHttp(FakeResponse(status=204)),
            FakeHttp(FakeResponse(status=500)),
            FakeHttp(FakeResponse(bad_json=True)),
            FakeHttp(raises=httpx.ConnectError("down")),
            FakeHttp(FakeResponse({"feedback": []})),
        ]
        for http in cases:
            c = make(http=http)
            self.assertEqual(run(c.loved_tracks()), [])
            self.assertIsNone(c._loved_cache)  # first scrobbles must not be hidden
            self.assertEqual(run(c.loved_tracks()), [])
            self.assertEqual(len(http.calls), 2)  # retried, not cached

    def test_top_tracks_204_is_empty(self):
        c = make(http=FakeHttp(FakeResponse(status=204)))
        self.assertEqual(run(c.top_tracks()), [])

    def test_top_tracks_request(self):
        http = FakeHttp(FakeResponse(TOP))
        rows = run(make(http=http).top_tracks())
        self.assertEqual(len(rows), 2)
        url, params = http.calls[0]
        self.assertTrue(url.endswith("/1/stats/user/listener/recordings"))
        self.assertEqual(params, {"range": "half_yearly", "count": 200})

    def test_similar_artists_request_and_limit(self):
        http = FakeHttp(FakeResponse(SIMILAR))
        c = make(http=http)
        self.assertEqual(run(c.similar_artists("")), [])
        rows = run(c.similar_artists(" seed ", limit=1))
        self.assertEqual([r["name"] for r in rows], ["Beta"])
        url, params = http.calls[0]
        self.assertIn("labs.api.listenbrainz.org/similar-artists/json", url)
        self.assertEqual(params["artist_mbids"], "seed")

    def test_similar_artists_error_is_empty(self):
        c = make(http=FakeHttp(FakeResponse(status=503)))
        self.assertEqual(run(c.similar_artists("seed")), [])

    def test_listen_count(self):
        self.assertEqual(run(make(http=FakeHttp(FakeResponse({"payload": {"count": 2}}))).listen_count()), 2)
        self.assertIsNone(run(make(http=FakeHttp(FakeResponse(status=404))).listen_count()))
        self.assertIsNone(run(make(http=FakeHttp(FakeResponse({"x": 1}))).listen_count()))

    def test_custom_url(self):
        import os
        os.environ["LISTENBRAINZ_URL"] = "https://lb.example/"
        try:
            self.assertEqual(ListenBrainzClient().api_root, "https://lb.example")
        finally:
            del os.environ["LISTENBRAINZ_URL"]


if __name__ == "__main__":
    unittest.main()
