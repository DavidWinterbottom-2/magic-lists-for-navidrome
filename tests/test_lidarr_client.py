"""Tests for the Lidarr write client — see tests/test_lastfm_client.py for the
sibling pattern this mirrors (no network; the httpx client is swapped for a
stub that returns canned payloads).

Run from the repo root:
    python -m unittest tests.test_lidarr_client
    python -m pytest tests/test_lidarr_client.py
"""

import unittest

import httpx

from backend.lidarr_client import (
    LidarrClient, build_add_artist_payload, pick_lookup_match
)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = b"body" if payload is not None else b""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        return self._payload


class FakeHttp:
    """Stands in for httpx.AsyncClient.request, replaying queued responses."""

    def __init__(self, responses=None, raises=None):
        self.responses = list(responses or [])
        self.raises = raises
        self.calls = []

    async def request(self, method, url, headers=None, params=None, json=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "params": params, "json": json})
        if self.raises:
            raise self.raises
        return self.responses.pop(0)


def _client(url="http://lidarr.local", api_key="key", quality="3", root="/music", http=None):
    client = LidarrClient()
    client.base_url = url
    client.api_key = api_key
    client.quality_profile_id = quality
    client.root_folder_path = root
    client.metadata_profile_id = "1"
    if http is not None:
        client.client = http
    return client


LOOKUP_MATCH = {"foreignArtistId": "mbid-1", "artistName": "boygenius"}
ADDED_ARTIST = {"id": 42, "artistName": "boygenius"}


class EnabledFlagTests(unittest.TestCase):
    def test_all_four_settings_enable_the_client(self):
        self.assertTrue(_client().enabled)

    def test_missing_api_key_disables_it(self):
        self.assertFalse(_client(api_key="").enabled)

    def test_missing_quality_profile_disables_it(self):
        self.assertFalse(_client(quality="").enabled)

    def test_missing_root_folder_disables_it(self):
        self.assertFalse(_client(root="").enabled)

    def test_missing_base_url_disables_it(self):
        self.assertFalse(_client(url="").enabled)


class BuildAddArtistPayloadTests(unittest.TestCase):
    def test_shapes_the_lookup_result_into_an_add_payload(self):
        payload = build_add_artist_payload(
            LOOKUP_MATCH, quality_profile_id=3, root_folder_path="/music"
        )
        self.assertEqual(payload["foreignArtistId"], "mbid-1")
        self.assertEqual(payload["artistName"], "boygenius")
        self.assertEqual(payload["qualityProfileId"], 3)
        self.assertEqual(payload["metadataProfileId"], 1)
        self.assertEqual(payload["rootFolderPath"], "/music")
        self.assertTrue(payload["monitored"])
        self.assertTrue(payload["addOptions"]["searchForMissingAlbums"])

    def test_falls_back_to_the_foreign_id_when_the_lookup_has_no_name(self):
        payload = build_add_artist_payload(
            {"foreignArtistId": "mbid-2"}, quality_profile_id=1, root_folder_path="/music"
        )
        self.assertEqual(payload["artistName"], "mbid-2")


class PickLookupMatchTests(unittest.TestCase):
    RESULTS = [
        {"foreignArtistId": "mbid-a", "artistName": "The Boy Geniuses"},
        {"foreignArtistId": "mbid-b", "artistName": "boygenius"},
    ]

    def test_prefers_an_exact_case_insensitive_match(self):
        match = pick_lookup_match(self.RESULTS, "Boygenius")
        self.assertEqual(match["foreignArtistId"], "mbid-b")

    def test_falls_back_to_the_top_result_with_no_exact_match(self):
        match = pick_lookup_match(self.RESULTS, "Some Other Band")
        self.assertEqual(match["foreignArtistId"], "mbid-a")

    def test_no_results_returns_none(self):
        self.assertIsNone(pick_lookup_match([], "Anyone"))


class AddArtistTests(unittest.IsolatedAsyncioTestCase):
    async def test_not_enabled_short_circuits_before_any_request(self):
        client = _client(api_key="", http=FakeHttp())
        result = await client.add_artist("boygenius")
        self.assertFalse(result["ok"])
        self.assertEqual(client.client.calls, [])

    async def test_a_failed_lookup_is_reported(self):
        http = FakeHttp(raises=Exception("connection reset"))
        result = await _client(http=http).add_artist("boygenius")
        self.assertFalse(result["ok"])
        self.assertIn("lookup failed", result["error"])

    async def test_no_match_is_reported(self):
        http = FakeHttp(responses=[FakeResponse([])])
        result = await _client(http=http).add_artist("boygenius")
        self.assertFalse(result["ok"])
        self.assertIn("No Lidarr match", result["error"])

    async def test_a_successful_add_returns_the_artist_name(self):
        http = FakeHttp(responses=[FakeResponse([LOOKUP_MATCH]), FakeResponse(ADDED_ARTIST)])
        result = await _client(http=http).add_artist("boygenius")
        self.assertEqual(result, {"ok": True, "artist_name": "boygenius"})

        add_call = http.calls[1]
        self.assertEqual(add_call["method"], "POST")
        self.assertEqual(add_call["json"]["foreignArtistId"], "mbid-1")
        self.assertEqual(add_call["json"]["qualityProfileId"], 3)
        self.assertEqual(add_call["json"]["rootFolderPath"], "/music")

    async def test_the_api_key_header_is_sent(self):
        http = FakeHttp(responses=[FakeResponse([LOOKUP_MATCH]), FakeResponse(ADDED_ARTIST)])
        await _client(http=http, api_key="secret").add_artist("boygenius")
        self.assertEqual(http.calls[0]["headers"]["X-Api-Key"], "secret")

    async def test_a_duplicate_add_is_reported_as_already_added(self):
        http = FakeHttp(responses=[
            FakeResponse([LOOKUP_MATCH]),
            FakeResponse({"message": "already exists"}, status=400),
        ])
        result = await _client(http=http).add_artist("boygenius")
        self.assertFalse(result["ok"])
        self.assertIn("already in Lidarr", result["error"])

    async def test_another_http_error_on_add_is_reported(self):
        http = FakeHttp(responses=[
            FakeResponse([LOOKUP_MATCH]),
            FakeResponse({}, status=500),
        ])
        result = await _client(http=http).add_artist("boygenius")
        self.assertFalse(result["ok"])
        self.assertIn("Lidarr add failed", result["error"])


if __name__ == "__main__":
    unittest.main()
