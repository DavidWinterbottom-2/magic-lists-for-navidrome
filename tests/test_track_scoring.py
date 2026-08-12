"""Tests for the engagement scoring signals and the smart-filter thresholds.

tests/test_track_selection.py covers the *diversity* half of this module — that
a barely-played library gets sampled rather than sliced. This covers the other
half: what each listening signal is actually worth, and when filtering kicks in
at all.

Run from the repo root:
    python -m unittest tests.test_track_scoring
    python -m pytest tests/test_track_scoring.py
"""

import unittest
from datetime import datetime, timedelta

from backend.track_scoring import (
    calculate_filter_threshold,
    filter_tracks_by_engagement,
    score_tracks_by_user_engagement,
    should_apply_smart_filtering,
)

# max_play_count of 100 makes the play component read directly as a percentage:
# a track played 40 times scores 40.
STATS = {"max_play_count": 100}


def _track(tid="1", **overrides):
    track = {"id": tid, "title": f"T{tid}", "artist": "A", "play_count": 0}
    track.update(overrides)
    return track


def _score_of(track, stats=STATS):
    """Score a single track and return just the number."""
    return score_tracks_by_user_engagement([track], stats)[0][0]


class PlayCountSignalTests(unittest.TestCase):
    def test_plays_are_normalised_against_the_library_maximum(self):
        self.assertEqual(_score_of(_track(play_count=40)), 40)

    def test_the_busiest_track_scores_full_marks(self):
        self.assertEqual(_score_of(_track(play_count=100)), 100)

    def test_an_unplayed_track_scores_nothing_from_plays(self):
        self.assertEqual(_score_of(_track(play_count=0)), 0)

    def test_a_library_with_no_plays_does_not_divide_by_zero(self):
        self.assertEqual(_score_of(_track(play_count=5), {"max_play_count": 0}), 0)


class BinaryAndRatingSignalTests(unittest.TestCase):
    def test_a_loved_track_is_worth_fifty(self):
        self.assertEqual(_score_of(_track(loved=True)), 50)

    def test_favorited_counts_the_same_as_loved(self):
        self.assertEqual(_score_of(_track(favorited=True)), 50)

    def test_a_star_rating_is_worth_ten_each(self):
        self.assertEqual(_score_of(_track(rating=4)), 40)

    def test_signals_stack(self):
        # 30 plays + loved (50) + 5 stars (50) = 130
        self.assertEqual(_score_of(_track(play_count=30, loved=True, rating=5)), 130)


class PlaylistAppearanceTests(unittest.TestCase):
    def test_each_appearance_is_worth_five(self):
        self.assertEqual(_score_of(_track(playlist_appearances=3)), 15)

    def test_appearances_are_capped_at_fifty(self):
        # Without the cap, a track on 40 playlists would outweigh every other
        # signal combined.
        self.assertEqual(_score_of(_track(playlist_appearances=40)), 50)


class RecencySignalTests(unittest.TestCase):
    def test_played_today_earns_the_full_bonus(self):
        self.assertEqual(_score_of(_track(last_played=datetime.now().isoformat())), 30)

    def test_the_bonus_decays_with_age(self):
        ten_days = (datetime.now() - timedelta(days=10)).isoformat()
        self.assertEqual(_score_of(_track(last_played=ten_days)), 20)

    def test_beyond_thirty_days_earns_nothing(self):
        long_ago = (datetime.now() - timedelta(days=200)).isoformat()
        self.assertEqual(_score_of(_track(last_played=long_ago)), 0)

    def test_a_datetime_object_works_as_well_as_a_string(self):
        self.assertEqual(_score_of(_track(last_played=datetime.now())), 30)

    def test_an_unparseable_date_is_skipped_rather_than_raising(self):
        # Navidrome has returned empty and malformed timestamps; a bad date must
        # not take down a whole playlist build.
        self.assertEqual(_score_of(_track(play_count=10, last_played="not-a-date")), 10)


class ScoreOrderingTests(unittest.TestCase):
    def test_results_come_back_highest_first(self):
        tracks = [_track("low", play_count=1), _track("high", play_count=90), _track("mid", play_count=40)]
        ranked = score_tracks_by_user_engagement(tracks, STATS)
        self.assertEqual([t["id"] for _, t in ranked], ["high", "mid", "low"])

    def test_every_track_is_returned(self):
        tracks = [_track(str(i)) for i in range(5)]
        self.assertEqual(len(score_tracks_by_user_engagement(tracks, STATS)), 5)

    def test_an_empty_pool_is_not_an_error(self):
        self.assertEqual(score_tracks_by_user_engagement([], STATS), [])


class FilterThresholdTests(unittest.TestCase):
    """The multiplier decides how much of the pool survives to reach the model."""

    def test_small_playlists_keep_the_widest_pool(self):
        self.assertEqual(calculate_filter_threshold(25), 10)
        self.assertEqual(calculate_filter_threshold(10), 10)

    def test_the_multiplier_drops_as_the_playlist_grows(self):
        self.assertEqual(calculate_filter_threshold(50), 8)
        self.assertEqual(calculate_filter_threshold(100), 6)

    def test_above_one_hundred_it_settles_at_the_floor(self):
        self.assertEqual(calculate_filter_threshold(200), 5)
        self.assertEqual(calculate_filter_threshold(600), 5)
        self.assertEqual(calculate_filter_threshold(1000), 5)

    def test_the_multiplier_never_widens_as_the_playlist_grows(self):
        # The regression this guards: `600 / size * 6` returned 18 at size 200,
        # a wider pool than the 6x used at size 100, so filtering never engaged
        # for large playlists. Monotonicity is the property that was violated.
        sizes = [10, 25, 26, 50, 51, 100, 101, 200, 500, 1000]
        multipliers = [calculate_filter_threshold(s) for s in sizes]
        self.assertEqual(multipliers, sorted(multipliers, reverse=True))
        self.assertTrue(all(m >= 5 for m in multipliers))


class SmartFilteringGateTests(unittest.TestCase):
    def test_a_pool_within_the_threshold_is_left_alone(self):
        self.assertFalse(should_apply_smart_filtering([_track()] * 250, 25))

    def test_a_pool_over_the_threshold_is_filtered(self):
        self.assertTrue(should_apply_smart_filtering([_track()] * 251, 25))

    def test_filtering_passes_a_small_pool_straight_through(self):
        pool = [_track(str(i)) for i in range(50)]
        self.assertIs(filter_tracks_by_engagement(pool, 25, STATS), pool)

    def test_filtering_trims_a_large_pool_to_the_threshold(self):
        pool = [_track(str(i), play_count=i) for i in range(400)]
        kept = filter_tracks_by_engagement(pool, 25, STATS)
        self.assertEqual(len(kept), 250)

    def test_the_best_tracks_survive_filtering(self):
        pool = [_track(str(i), play_count=i) for i in range(400)]
        kept_ids = {t["id"] for t in filter_tracks_by_engagement(pool, 25, STATS)}
        # The 50 most-played must all be kept — they outscore everything below.
        self.assertTrue({str(i) for i in range(350, 400)}.issubset(kept_ids))


if __name__ == "__main__":
    unittest.main()
