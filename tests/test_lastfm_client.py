"""Tests for the Last.fm HTTP surface — the request layer, not the parsers.

tests/test_lastfm.py covers the pure parsing and key-normalisation functions.
This covers the client methods around them: that every failure mode degrades to
"no Last.fm data" instead of raising, and that loved tracks are cached.

No network: the httpx client is swapped for a stub that returns canned payloads.

Run from the repo root:
    python -m unittest tests.test_lastfm_client
    python -m pytest tests/test_lastfm_client.py
"""

import unittest

import httpx

from backend.lastfm_client import LastfmClient, loved_key


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        return self._payload


class FakeHttp:
    """Stands in for httpx.AsyncClient, recording calls and replaying answers."""

    def __init__(self, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append(params or {})
        if self.raises:
            raise self.raises
        return self.response


def _client(api_key="key", username="listener", http=None):
    client = LastfmClient()
    client.api_key = api_key
    client.username = username
    client._loved_cache = None
    if http is not None:
        client.client = http
    return client


LOVED_PAYLOAD = {
    "lovedtracks": {
        "track": [
            {"name": "Halo", "artist": {"name": "Beyoncé"}},
            {"name": "Hey Jude", "artist": {"name": "The Beatles"}},
        ]
    }
}


class EnabledFlagTests(unittest.TestCase):
    def test_an_api_key_alone_enables_the_global_methods(self):
        client = _client(api_key="key", username="")
        self.assertTrue(client.enabled)
        self.assertFalse(client.user_enabled)

    def test_reading_a_listeners_history_needs_a_username_too(self):
        self.assertTrue(_client().user_enabled)

    def test_no_key_disables_everything(self):
        client = _client(api_key="", username="listener")
        self.assertFalse(client.enabled)
        self.assertFalse(client.user_enabled)


class RequestLayerTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_successful_call_returns_the_payload(self):
        http = FakeHttp(FakeResponse({"ok": True}))
        result = await _client(http=http)._get("artist.getSimilar", {"artist": "Alpha"})
        self.assertEqual(result, {"ok": True})

    async def test_the_api_key_and_format_are_always_sent(self):
        http = FakeHttp(FakeResponse({}))
        await _client(http=http)._get("artist.getSimilar", {"artist": "Alpha"})
        sent = http.calls[0]
        self.assertEqual(sent["api_key"], "key")
        self.assertEqual(sent["format"], "json")
        self.assertEqual(sent["method"], "artist.getSimilar")

    async def test_no_api_key_short_circuits_before_any_request(self):
        http = FakeHttp(FakeResponse({"ok": True}))
        result = await _client(api_key="", http=http)._get("artist.getSimilar", {})
        self.assertIsNone(result)
        self.assertEqual(http.calls, [])

    async def test_an_api_level_error_payload_is_treated_as_no_data(self):
        # Last.fm returns HTTP 200 with an error body for a bad key (code 10).
        http = FakeHttp(FakeResponse({"error": 10, "message": "Invalid API key"}))
        self.assertIsNone(await _client(http=http)._get("user.getLovedTracks", {}))

    async def test_an_http_error_is_swallowed(self):
        http = FakeHttp(FakeResponse({}, status=500))
        self.assertIsNone(await _client(http=http)._get("user.getLovedTracks", {}))

    async def test_a_transport_failure_is_swallowed(self):
        http = FakeHttp(raises=Exception("connection reset"))
        self.assertIsNone(await _client(http=http)._get("user.getLovedTracks", {}))


class LovedTrackTests(unittest.IsolatedAsyncioTestCase):
    async def test_loved_tracks_become_normalised_keys(self):
        client = _client(http=FakeHttp(FakeResponse(LOVED_PAYLOAD)))
        keys = await client.loved_track_keys()
        self.assertIn(loved_key("Beyoncé", "Halo"), keys)
        self.assertIn(loved_key("The Beatles", "Hey Jude"), keys)

    async def test_without_a_username_no_call_is_made(self):
        http = FakeHttp(FakeResponse(LOVED_PAYLOAD))
        self.assertEqual(await _client(username="", http=http).loved_track_keys(), set())
        self.assertEqual(http.calls, [])

    async def test_the_result_is_cached_across_calls(self):
        http = FakeHttp(FakeResponse(LOVED_PAYLOAD))
        client = _client(http=http)
        await client.loved_track_keys()
        await client.loved_track_keys()
        self.assertEqual(len(http.calls), 1)

    async def test_an_empty_result_is_cached_too(self):
        # A private profile must not trigger a fresh lookup on every build.
        http = FakeHttp(FakeResponse({}))
        client = _client(http=http)
        self.assertEqual(await client.loved_track_keys(), set())
        await client.loved_track_keys()
        self.assertEqual(len(http.calls), 1)


class SimilarArtistTests(unittest.IsolatedAsyncioTestCase):
    PAYLOAD = {"similarartists": {"artist": [{"name": "Beta", "match": "0.9"}]}}

    async def test_similar_artists_are_parsed(self):
        client = _client(http=FakeHttp(FakeResponse(self.PAYLOAD)))
        self.assertEqual([a["name"] for a in await client.similar_artists("Alpha")], ["Beta"])

    async def test_it_needs_only_an_api_key_not_a_username(self):
        client = _client(username="", http=FakeHttp(FakeResponse(self.PAYLOAD)))
        self.assertEqual(len(await client.similar_artists("Alpha")), 1)

    async def test_a_blank_artist_name_makes_no_request(self):
        http = FakeHttp(FakeResponse(self.PAYLOAD))
        self.assertEqual(await _client(http=http).similar_artists("  "), [])
        self.assertEqual(http.calls, [])

    async def test_a_failed_lookup_returns_an_empty_list(self):
        http = FakeHttp(raises=Exception("down"))
        self.assertEqual(await _client(http=http).similar_artists("Alpha"), [])


class TopTrackTests(unittest.IsolatedAsyncioTestCase):
    PAYLOAD = {"toptracks": {"track": [{"name": "Halo", "artist": {"name": "Beyoncé"}}]}}

    async def test_top_tracks_are_parsed(self):
        client = _client(http=FakeHttp(FakeResponse(self.PAYLOAD)))
        self.assertEqual([t["title"] for t in await client.top_tracks()], ["Halo"])

    async def test_the_requested_period_is_passed_through(self):
        http = FakeHttp(FakeResponse(self.PAYLOAD))
        await _client(http=http).top_tracks(period="12month")
        self.assertEqual(http.calls[0]["period"], "12month")

    async def test_without_a_username_no_call_is_made(self):
        http = FakeHttp(FakeResponse(self.PAYLOAD))
        self.assertEqual(await _client(username="", http=http).top_tracks(), [])
        self.assertEqual(http.calls, [])


if __name__ == "__main__":
    unittest.main()
