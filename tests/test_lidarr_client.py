"""Tests for the Lidarr write client — see tests/test_lastfm_client.py for the
sibling pattern this mirrors (no network; the httpx client is swapped for a
stub that returns canned payloads).

Run from the repo root:
    python -m unittest tests.test_lidarr_client
    python -m pytest tests/test_lidarr_client.py
"""

import json
import unittest

import httpx

from backend.lidarr_client import (
    LidarrClient, build_add_artist_payload, describe_lidarr_error,
    normalise_album_title, pick_album_match, pick_lookup_match
)


class FakeResponse:
    def __init__(self, payload, status=200, text=None, json_raises=False):
        self._payload = payload
        self.status_code = status
        self.content = b"body" if payload is not None else b""
        self._json_raises = json_raises
        self.text = text if text is not None else (json.dumps(payload) if payload is not None else "")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        if self._json_raises:
            raise ValueError("not JSON")
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


async def _no_sleep(_seconds):
    return None


def _client(url="http://lidarr.local", api_key="key", quality="3", root="/music", http=None):
    client = LidarrClient()
    client.base_url = url
    client.api_key = api_key
    client.quality_profile_id = quality
    client.root_folder_path = root
    client.metadata_profile_id = "1"
    client._sleep = _no_sleep
    if http is not None:
        client.client = http
    return client


LOOKUP_MATCH = {"foreignArtistId": "mbid-1", "artistName": "boygenius"}
ADDED_ARTIST = {"id": 42, "artistName": "boygenius"}
ALBUM = {"id": 99, "title": "the record"}


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


class DescribeLidarrErrorTests(unittest.TestCase):
    def _http_error(self, response):
        return httpx.HTTPStatusError("boom", request=None, response=response)

    def test_extracts_validation_error_messages_from_a_list_body(self):
        response = FakeResponse(
            [{"propertyName": "RootFolderPath", "errorMessage": "Root folder does not exist"}],
            status=500,
        )
        self.assertEqual(
            describe_lidarr_error(self._http_error(response)),
            "Root folder does not exist",
        )

    def test_joins_multiple_validation_messages(self):
        response = FakeResponse(
            [{"errorMessage": "Root folder does not exist"}, {"errorMessage": "Bad profile"}],
            status=500,
        )
        self.assertEqual(
            describe_lidarr_error(self._http_error(response)),
            "Root folder does not exist; Bad profile",
        )

    def test_extracts_a_plain_message_body(self):
        response = FakeResponse({"message": "Something went wrong"}, status=500)
        self.assertEqual(describe_lidarr_error(self._http_error(response)), "Something went wrong")

    def test_falls_back_to_raw_text_when_the_body_is_not_json(self):
        response = FakeResponse(None, status=500, text="<html>502</html>", json_raises=True)
        self.assertEqual(describe_lidarr_error(self._http_error(response)), "<html>502</html>")

    def test_falls_back_to_describe_exception_for_a_non_http_error(self):
        self.assertIn("boom", describe_lidarr_error(Exception("boom")))

    def test_falls_back_to_describe_exception_when_body_and_text_are_both_empty(self):
        response = FakeResponse({}, status=500, text="")
        result = describe_lidarr_error(self._http_error(response))
        self.assertTrue(result)  # never an empty string


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
        self.assertEqual(add_call["json"]["addOptions"]["monitor"], "all")
        self.assertTrue(add_call["json"]["addOptions"]["searchForMissingAlbums"])

    async def test_the_api_key_header_is_sent(self):
        http = FakeHttp(responses=[FakeResponse([LOOKUP_MATCH]), FakeResponse(ADDED_ARTIST)])
        await _client(http=http, api_key="secret").add_artist("boygenius")
        self.assertEqual(http.calls[0]["headers"]["X-Api-Key"], "secret")

    async def test_a_non_numeric_quality_profile_id_is_reported_not_crashed(self):
        # Lidarr's built-in default profile is literally named "Any" — an easy
        # value to mistake for the numeric id LIDARR_QUALITY_PROFILE_ID needs.
        http = FakeHttp(responses=[FakeResponse([LOOKUP_MATCH])])
        client = _client(http=http, quality="Any")
        result = await client.add_artist("boygenius")
        self.assertFalse(result["ok"])
        self.assertIn("numeric profile id", result["error"])
        # No add attempt was made — only the lookup call happened.
        self.assertEqual(len(http.calls), 1)

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


class PickAlbumMatchTests(unittest.TestCase):
    ALBUMS = [
        {"id": 1, "title": "Other Album"},
        {"id": 99, "title": "the record (Deluxe Edition)"},
    ]

    def test_prefers_an_exact_case_insensitive_match(self):
        albums = [{"id": 1, "title": "The Record"}]
        self.assertEqual(pick_album_match(albums, "the record")["id"], 1)

    def test_falls_back_to_a_normalised_match_ignoring_a_bracketed_suffix(self):
        self.assertEqual(pick_album_match(self.ALBUMS, "The Record")["id"], 99)

    def test_no_match_returns_none_rather_than_guessing(self):
        self.assertIsNone(pick_album_match(self.ALBUMS, "Some Other Thing"))

    def test_a_blank_title_returns_none(self):
        self.assertIsNone(pick_album_match(self.ALBUMS, ""))


class NormaliseAlbumTitleTests(unittest.TestCase):
    def test_strips_a_bracketed_suffix_case_and_punctuation(self):
        self.assertEqual(
            normalise_album_title("The Record (Deluxe Edition)"),
            normalise_album_title("the record"),
        )

    def test_a_none_title_normalises_to_empty(self):
        self.assertEqual(normalise_album_title(None), "")


class AddAlbumTests(unittest.IsolatedAsyncioTestCase):
    async def test_adds_the_artist_with_nothing_monitored(self):
        http = FakeHttp(responses=[
            FakeResponse([LOOKUP_MATCH]),
            FakeResponse(ADDED_ARTIST),
            FakeResponse([ALBUM]),
            FakeResponse(None),
            FakeResponse(None),
        ])
        result = await _client(http=http).add_album("boygenius", "the record")
        self.assertEqual(result, {"ok": True, "artist_name": "boygenius", "album_title": "the record"})

        add_call = http.calls[1]
        self.assertEqual(add_call["json"]["addOptions"]["monitor"], "none")
        self.assertFalse(add_call["json"]["addOptions"]["searchForMissingAlbums"])

    async def test_monitors_and_searches_the_matched_album(self):
        http = FakeHttp(responses=[
            FakeResponse([LOOKUP_MATCH]),
            FakeResponse(ADDED_ARTIST),
            FakeResponse([ALBUM]),
            FakeResponse(None),
            FakeResponse(None),
        ])
        await _client(http=http).add_album("boygenius", "the record")

        monitor_call = http.calls[3]
        self.assertEqual(monitor_call["method"], "PUT")
        self.assertEqual(monitor_call["url"], "http://lidarr.local/api/v1/album/monitor")
        self.assertEqual(monitor_call["json"], {"albumIds": [99], "monitored": True})

        search_call = http.calls[4]
        self.assertEqual(search_call["method"], "POST")
        self.assertEqual(search_call["url"], "http://lidarr.local/api/v1/command")
        self.assertEqual(search_call["json"], {"name": "AlbumSearch", "albumIds": [99]})

    async def test_polls_until_the_album_appears(self):
        http = FakeHttp(responses=[
            FakeResponse([LOOKUP_MATCH]),
            FakeResponse(ADDED_ARTIST),
            FakeResponse([]),
            FakeResponse([]),
            FakeResponse([ALBUM]),
            FakeResponse(None),
            FakeResponse(None),
        ])
        result = await _client(http=http).add_album("boygenius", "the record")
        self.assertEqual(result["album_title"], "the record")

    async def test_gives_up_after_max_attempts_and_reports_a_warning(self):
        http = FakeHttp(responses=[
            FakeResponse([LOOKUP_MATCH]),
            FakeResponse(ADDED_ARTIST),
            *[FakeResponse([]) for _ in range(10)],
        ])
        result = await _client(http=http).add_album("boygenius", "the record")
        self.assertTrue(result["ok"])
        self.assertIsNone(result["album_title"])
        self.assertIn("couldn't find", result["warning"])
        # No monitor/search attempt was made without a matched album.
        self.assertEqual(len(http.calls), 12)

    async def test_a_duplicate_artist_is_reused_to_add_the_album(self):
        existing_artist = {"id": 7, "artistName": "boygenius", "foreignArtistId": "mbid-1"}
        http = FakeHttp(responses=[
            FakeResponse([LOOKUP_MATCH]),
            FakeResponse({"message": "already exists"}, status=400),
            FakeResponse([existing_artist]),
            FakeResponse([ALBUM]),
            FakeResponse(None),
            FakeResponse(None),
        ])
        result = await _client(http=http).add_album("boygenius", "the record")
        self.assertEqual(result, {"ok": True, "artist_name": "boygenius", "album_title": "the record"})

        # The album lookup used the existing artist's id, not a fresh add.
        album_call = http.calls[3]
        self.assertEqual(album_call["params"], {"artistId": 7})

    async def test_a_duplicate_artist_that_cannot_be_found_is_reported(self):
        http = FakeHttp(responses=[
            FakeResponse([LOOKUP_MATCH]),
            FakeResponse({"message": "already exists"}, status=400),
            FakeResponse([]),
        ])
        result = await _client(http=http).add_album("boygenius", "the record")
        self.assertFalse(result["ok"])
        self.assertIn("couldn't be found", result["error"])

    async def test_a_failure_to_monitor_the_found_album_is_reported_as_a_warning(self):
        http = FakeHttp(responses=[
            FakeResponse([LOOKUP_MATCH]),
            FakeResponse(ADDED_ARTIST),
            FakeResponse([ALBUM]),
            FakeResponse({}, status=500),
        ])
        result = await _client(http=http).add_album("boygenius", "the record")
        self.assertTrue(result["ok"])
        self.assertIsNone(result["album_title"])
        self.assertIn("couldn't monitor", result["warning"])

    async def test_not_enabled_short_circuits_before_any_request(self):
        client = _client(api_key="", http=FakeHttp())
        result = await client.add_album("boygenius", "the record")
        self.assertFalse(result["ok"])
        self.assertEqual(client.client.calls, [])

    async def test_a_non_numeric_quality_profile_id_is_reported_not_crashed(self):
        http = FakeHttp(responses=[FakeResponse([LOOKUP_MATCH])])
        result = await _client(http=http, quality="Any").add_album("boygenius", "the record")
        self.assertFalse(result["ok"])
        self.assertIn("numeric profile id", result["error"])


if __name__ == "__main__":
    unittest.main()
