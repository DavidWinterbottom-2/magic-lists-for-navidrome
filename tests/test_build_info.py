"""Tests for the build detail a playlist keeps about its last rebuild.

Creating a station shows reasoning, a shortfall notice and album suggestions.
A rebuild — pressed in the UI or run overnight by the scheduler — has to show
the same thing, which means storing it rather than only returning it once.

Run from the repo root:
    python -m unittest tests.test_build_info
"""

import os
import tempfile
import unittest

from backend.database import DatabaseManager
from backend.errors import describe_exception
from backend.radio import build_shortfall


class DescribeExceptionTests(unittest.TestCase):
    """A timeout must not log an empty reason."""

    def test_uses_the_message_when_there_is_one(self):
        self.assertEqual(describe_exception(ValueError("boom")), "boom")

    def test_falls_back_to_the_type_name_when_blank(self):
        # This is the real case: httpx timeouts stringify to ""
        import httpx
        self.assertEqual(describe_exception(httpx.ReadTimeout("")), "ReadTimeout")

    def test_whitespace_only_message_is_treated_as_blank(self):
        self.assertEqual(describe_exception(ValueError("   ")), "ValueError")


class ShortfallWarningTests(unittest.TestCase):
    """A full-length station built from a degraded pool still needs reporting."""

    def test_warnings_surface_even_when_nothing_is_short(self):
        report = build_shortfall(
            requested=25, delivered=25, candidate_pool_size=300,
            warnings=["The similar-songs lookup failed (ReadTimeout)."]
        )
        self.assertFalse(report["is_short"])
        self.assertEqual(len(report["warnings"]), 1)
        self.assertIn("ReadTimeout", report["message"])

    def test_no_warnings_means_no_message(self):
        report = build_shortfall(requested=25, delivered=25, candidate_pool_size=300)
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["message"], "")

    def test_a_recall_failure_explains_a_short_station_better_than_a_thin_library(self):
        report = build_shortfall(
            requested=25, delivered=6, candidate_pool_size=300,
            warnings=["The similar-songs lookup failed (ReadTimeout)."]
        )
        self.assertTrue(report["is_short"])
        self.assertIn("ReadTimeout", report["message"])
        self.assertNotIn("Adding the albums below", report["message"])


class BuildInfoPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """Round-trip through a real (temporary) SQLite database."""

    async def asyncSetUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DatabaseManager(self.path)
        await self.db.init_db()

    async def asyncTearDown(self):
        os.unlink(self.path)

    SUGGESTIONS = [{"artist": "Bon Iver", "album": "For Emma", "year": 2007,
                    "lidarr_url": "https://lidarr.example.com/add/search?term=Bon+Iver"}]
    BUILD_INFO = {"requested": 25, "delivered": 12, "missing": 13, "is_short": True,
                  "warnings": ["pool degraded"], "message": "short"}

    async def _create(self, **kwargs):
        return await self.db.create_playlist(
            artist_id="radio:artist:A1", playlist_name="Test Radio",
            songs=[{"title": "T", "artist": "A", "album": "B"}],
            navidrome_playlist_id="nav-1", **kwargs
        )

    async def test_build_info_survives_a_round_trip(self):
        created = await self._create(
            album_suggestions=self.SUGGESTIONS, build_info=self.BUILD_INFO)
        stored = await self.db.get_playlist_by_id_with_schedule_info(created.id)
        self.assertEqual(stored["album_suggestions"], self.SUGGESTIONS)
        self.assertEqual(stored["build_info"], self.BUILD_INFO)

    async def test_listing_returns_it_too(self):
        await self._create(album_suggestions=self.SUGGESTIONS, build_info=self.BUILD_INFO)
        listed = await self.db.get_all_playlists_with_schedule_info()
        self.assertEqual(listed[0]["build_info"]["delivered"], 12)
        self.assertEqual(listed[0]["album_suggestions"][0]["artist"], "Bon Iver")

    async def test_absent_build_info_reads_back_as_empty_not_an_error(self):
        # Playlists created before these columns existed, and non-radio types
        created = await self._create()
        stored = await self.db.get_playlist_by_id_with_schedule_info(created.id)
        self.assertEqual(stored["album_suggestions"], [])
        self.assertIsNone(stored["build_info"])

    async def test_a_refresh_replaces_the_stored_build_info(self):
        created = await self._create(album_suggestions=[], build_info={"is_short": False})
        await self.db.update_playlist_content(
            navidrome_playlist_id="nav-1",
            songs=[{"title": "New", "artist": "A", "album": "B"}],
            reasoning="fresh",
            album_suggestions=self.SUGGESTIONS,
            build_info=self.BUILD_INFO,
        )
        stored = await self.db.get_playlist_by_id_with_schedule_info(created.id)
        self.assertEqual(stored["reasoning"], "fresh")
        self.assertTrue(stored["build_info"]["is_short"])
        self.assertEqual(len(stored["album_suggestions"]), 1)

    async def test_a_refresh_that_supplies_neither_leaves_them_alone(self):
        # This Is / Re-Discover refreshes produce no suggestions, and must not
        # wipe what a previous build stored.
        created = await self._create(
            album_suggestions=self.SUGGESTIONS, build_info=self.BUILD_INFO)
        await self.db.update_playlist_content(
            navidrome_playlist_id="nav-1",
            songs=[{"title": "New", "artist": "A", "album": "B"}],
            reasoning="fresh",
        )
        stored = await self.db.get_playlist_by_id_with_schedule_info(created.id)
        self.assertEqual(stored["album_suggestions"], self.SUGGESTIONS)
        self.assertEqual(stored["build_info"], self.BUILD_INFO)



class PlaylistLengthTests(unittest.IsolatedAsyncioTestCase):
    """The target length must survive a short build, and be changeable."""

    async def asyncSetUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.db = DatabaseManager(self.path)
        await self.db.init_db()

    async def asyncTearDown(self):
        os.unlink(self.path)

    async def test_a_short_build_does_not_shrink_the_target(self):
        # Asked for 25, only 5 could be found — the target stays 25 so the next
        # rebuild tries for 25 again rather than settling at 5 forever.
        created = await self.db.create_playlist(
            artist_id="radio:artist:A1", playlist_name="Short Radio",
            songs=[{"title": f"T{i}", "artist": "A", "album": "B"} for i in range(5)],
            navidrome_playlist_id="nav-1", playlist_length=25,
        )
        stored = await self.db.get_playlist_by_id_with_schedule_info(created.id)
        self.assertEqual(stored["playlist_length"], 25)
        self.assertEqual(len(stored["songs"]), 5)

    async def test_a_refresh_leaves_the_target_alone(self):
        created = await self.db.create_playlist(
            artist_id="radio:artist:A1", playlist_name="R", songs=[],
            navidrome_playlist_id="nav-1", playlist_length=25,
        )
        await self.db.update_playlist_content(
            navidrome_playlist_id="nav-1",
            songs=[{"title": "T", "artist": "A", "album": "B"}],
            reasoning="r",
        )
        stored = await self.db.get_playlist_by_id_with_schedule_info(created.id)
        self.assertEqual(stored["playlist_length"], 25)

    async def test_the_target_can_be_changed(self):
        created = await self.db.create_playlist(
            artist_id="radio:artist:A1", playlist_name="R", songs=[],
            navidrome_playlist_id="nav-1", playlist_length=25,
        )
        self.assertTrue(await self.db.update_playlist_length(created.id, 50))
        stored = await self.db.get_playlist_by_id_with_schedule_info(created.id)
        self.assertEqual(stored["playlist_length"], 50)

    async def test_changing_an_unknown_playlist_reports_failure(self):
        self.assertFalse(await self.db.update_playlist_length(9999, 50))

if __name__ == "__main__":
    unittest.main()
