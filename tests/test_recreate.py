"""Tests for the manual "Recreate" path in playlist management.

Recreating reuses the scheduler's own refresh functions, so the pieces that
needed to change for a manual run are: identifying which refresh path rebuilds a
given playlist, and coping with a playlist that has no schedule behind it.

Run from the repo root:
    python -m unittest tests.test_recreate
"""

import unittest

from backend.main import ManualRefreshTarget, advance_schedule, infer_playlist_type


class InferPlaylistTypeTests(unittest.TestCase):
    """Which refresh path rebuilds this playlist?"""

    def test_scheduled_type_wins_when_present(self):
        playlist = {"playlist_type": "rediscover", "artist_id": "radio:artist:A1"}
        self.assertEqual(infer_playlist_type(playlist), "rediscover")

    def test_radio_is_recognised_from_its_seed_key(self):
        self.assertEqual(
            infer_playlist_type({"artist_id": "radio:artist:7dB07x8Q2P9jPvGeDHxIFa"}), "radio")
        self.assertEqual(
            infer_playlist_type({"artist_id": "radio:song:BhDkeuI4q2fzAZww5zLVV9"}), "radio")

    def test_rediscover_sentinels_are_recognised(self):
        self.assertEqual(infer_playlist_type({"artist_id": "rediscover"}), "rediscover")
        self.assertEqual(infer_playlist_type({"artist_id": "rediscover_v2"}), "rediscover")

    def test_a_bare_artist_id_is_treated_as_this_is(self):
        self.assertEqual(infer_playlist_type({"artist_id": "0U2p6YCVK3ebbITLoUwHY2"}), "this_is")

    def test_missing_artist_id_does_not_raise(self):
        self.assertEqual(infer_playlist_type({}), "this_is")
        self.assertEqual(infer_playlist_type({"artist_id": None}), "this_is")

    def test_null_scheduled_type_falls_through_to_inference(self):
        # LEFT JOIN gives playlist_type=None for an unscheduled playlist
        playlist = {"playlist_type": None, "artist_id": "radio:artist:A1"}
        self.assertEqual(infer_playlist_type(playlist), "radio")


class ManualRefreshTargetTests(unittest.TestCase):

    def test_unscheduled_target_has_no_schedule_id(self):
        target = ManualRefreshTarget("np1", "radio")
        self.assertIsNone(target.id)
        self.assertEqual(target.navidrome_playlist_id, "np1")
        self.assertEqual(target.refresh_frequency, "none")

    def test_scheduled_target_carries_the_schedule_through(self):
        target = ManualRefreshTarget("np1", "radio", refresh_frequency="daily", scheduled_id=7)
        self.assertEqual(target.id, 7)
        self.assertEqual(target.refresh_frequency, "daily")

    def test_none_frequency_normalises(self):
        self.assertEqual(ManualRefreshTarget("np1", "radio", refresh_frequency=None).refresh_frequency, "none")


class _FakeDb:
    """Records next-refresh advances so the guard can be asserted on."""

    def __init__(self):
        self.advanced = []

    async def update_scheduled_playlist_next_refresh(self, scheduled_id, next_refresh):
        self.advanced.append((scheduled_id, next_refresh))
        return True


class AdvanceScheduleTests(unittest.IsolatedAsyncioTestCase):
    """A manual rebuild must not invent a schedule that was never there."""

    async def test_unscheduled_playlist_is_not_advanced(self):
        db = _FakeDb()
        await advance_schedule(ManualRefreshTarget("np1", "radio"), db, "test")
        self.assertEqual(db.advanced, [], "nothing to advance without a schedule")

    async def test_paused_schedule_is_not_advanced(self):
        db = _FakeDb()
        target = ManualRefreshTarget("np1", "radio", refresh_frequency="none", scheduled_id=3)
        await advance_schedule(target, db, "test")
        self.assertEqual(db.advanced, [])

        target.refresh_frequency = "never"
        await advance_schedule(target, db, "test")
        self.assertEqual(db.advanced, [])

    async def test_scheduled_playlist_is_advanced(self):
        db = _FakeDb()
        target = ManualRefreshTarget("np1", "radio", refresh_frequency="daily", scheduled_id=3)
        await advance_schedule(target, db, "test")
        self.assertEqual(len(db.advanced), 1)
        self.assertEqual(db.advanced[0][0], 3)

    async def test_a_plain_scheduled_row_still_works(self):
        # The scheduler passes its own ScheduledPlaylist object, not our stand-in
        class Row:
            id = 11
            refresh_frequency = "weekly"
            navidrome_playlist_id = "np9"

        db = _FakeDb()
        await advance_schedule(Row(), db, "test")
        self.assertEqual(db.advanced[0][0], 11)


if __name__ == "__main__":
    unittest.main()
