"""Tests for how candidate tracks are narrowed before AI curation.

The filter used to take a plain top-N slice of an engagement ranking. On a
library that hasn't been played much almost everything scores zero, so a stable
sort returned the identical subset every time — every rebuild sent the AI the
same candidates and produced the same songs.

Run from the repo root:
    python -m unittest tests.test_track_selection
"""

import random
import unittest

from backend.track_scoring import (
    FULL_ENGAGEMENT_COVERAGE,
    effective_library_stats,
    filter_tracks_for_this_is_playlist,
    measure_play_coverage,
    select_with_tiebreak,
)


def _track(tid, play_count=0):
    return {"id": tid, "title": f"T{tid}", "artist": "A", "album": "B",
            "play_count": play_count}


class PlayCoverageTests(unittest.TestCase):

    def test_counts_the_played_fraction(self):
        tracks = [_track(1, 5), _track(2), _track(3), _track(4)]
        self.assertEqual(measure_play_coverage(tracks), 0.25)

    def test_empty_pool_is_zero_not_an_error(self):
        self.assertEqual(measure_play_coverage([]), 0.0)

    def test_none_play_counts_count_as_unplayed(self):
        self.assertEqual(measure_play_coverage([{"play_count": None}]), 0.0)


class EffectiveStatsTests(unittest.TestCase):
    """Normalise against the pool, not the estimated library maximum."""

    def test_pool_maximum_replaces_the_estimate(self):
        # get_library_stats invents 100 when the server reports no total
        stats = effective_library_stats([_track(1, 5), _track(2, 2)],
                                        {"max_play_count": 100})
        self.assertEqual(stats["max_play_count"], 5)

    def test_unplayed_pool_avoids_a_divide_by_zero(self):
        stats = effective_library_stats([_track(1), _track(2)], {})
        self.assertEqual(stats["max_play_count"], 1)

    def test_other_stats_are_preserved(self):
        stats = effective_library_stats([_track(1, 3)],
                                        {"max_play_count": 100, "max_playlist_appearances": 10})
        self.assertEqual(stats["max_playlist_appearances"], 10)


class TiebreakSelectionTests(unittest.TestCase):

    def test_higher_scores_always_win(self):
        scored = [(50.0, _track(1)), (10.0, _track(2))] + [(0.0, _track(i)) for i in range(3, 20)]
        picked = select_with_tiebreak(scored, 2, rng=random.Random(1))
        self.assertEqual([t["id"] for t in picked], [1, 2])

    def test_tied_tracks_are_sampled_not_sliced(self):
        # 100 tracks all scoring 0 — the pathological real-world case
        scored = [(0.0, _track(i)) for i in range(100)]
        first = [t["id"] for t in select_with_tiebreak(scored, 10, rng=random.Random(1))]
        second = [t["id"] for t in select_with_tiebreak(scored, 10, rng=random.Random(2))]
        self.assertNotEqual(first, second, "different runs must pick different tracks")
        self.assertEqual(len(first), 10)

    def test_scored_tracks_survive_even_when_the_rest_is_sampled(self):
        scored = [(50.0, _track(999))] + [(0.0, _track(i)) for i in range(50)]
        for seed in range(5):
            picked = select_with_tiebreak(scored, 10, rng=random.Random(seed))
            self.assertIn(999, [t["id"] for t in picked], "a genuinely better track is never dropped")

    def test_asking_for_everything_returns_everything(self):
        scored = [(0.0, _track(i)) for i in range(5)]
        self.assertEqual(len(select_with_tiebreak(scored, 10)), 5)

    def test_asking_for_nothing_returns_nothing(self):
        self.assertEqual(select_with_tiebreak([(0.0, _track(1))], 0), [])


class FilterDiversityTests(unittest.TestCase):
    """End-to-end: the same pool must not yield the same candidates every time."""

    def _pool(self, size=400, played=0):
        return ([_track(i, play_count=3) for i in range(played)]
                + [_track(1000 + i) for i in range(size - played)])

    def test_unplayed_library_gives_a_different_set_each_time(self):
        pool = self._pool()
        first, meta = filter_tracks_for_this_is_playlist(pool, 25, {"max_play_count": 100})
        second, _ = filter_tracks_for_this_is_playlist(pool, 25, {"max_play_count": 100})
        self.assertTrue(meta["filtered"])
        self.assertNotEqual([t["id"] for t in first], [t["id"] for t in second])

    def test_a_barely_played_library_leans_on_sampling(self):
        # 2% coverage -> almost the whole set is sampled rather than ranked
        _, meta = filter_tracks_for_this_is_playlist(self._pool(played=8), 25,
                                                     {"max_play_count": 100})
        self.assertLess(meta["play_coverage"], FULL_ENGAGEMENT_COVERAGE)
        self.assertGreater(meta["sampled_count"], meta["engagement_count"])

    def test_a_well_played_library_trusts_engagement(self):
        _, meta = filter_tracks_for_this_is_playlist(self._pool(played=400), 25,
                                                     {"max_play_count": 100})
        self.assertEqual(meta["play_coverage"], 1.0)
        self.assertEqual(meta["sampled_count"], 0, "no sampling needed once plays are meaningful")

    def test_the_requested_number_of_candidates_is_still_returned(self):
        pool = self._pool()
        filtered, meta = filter_tracks_for_this_is_playlist(pool, 25, {"max_play_count": 100})
        self.assertEqual(len(filtered), meta["sent_count"])
        self.assertEqual(len(filtered), len({id(t) for t in filtered}), "no duplicates")

    def test_a_small_pool_is_passed_through_untouched(self):
        pool = self._pool(size=10)
        filtered, meta = filter_tracks_for_this_is_playlist(pool, 25, {"max_play_count": 100})
        self.assertFalse(meta["filtered"])
        self.assertEqual(len(filtered), 10)


if __name__ == "__main__":
    unittest.main()
