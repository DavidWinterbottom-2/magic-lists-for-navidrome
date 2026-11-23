"""Tests for provider-neutral name matching and the loved-track signal.

Covers key folding, lenient MBID-or-name matching in `mark_loved`, and
`apply_loved_signal` wiring (ListenBrainz configured / unconfigured / failing).

    python -m pytest tests/test_name_matching.py
"""

import asyncio
import unittest
from unittest import mock

from backend import main
from backend.name_matching import loved_key, mark_loved, normalise_name


class LovedKeyTests(unittest.TestCase):
    def test_folds_case_articles_accents_punctuation(self):
        self.assertEqual(loved_key("The Beatles", "Hey Jude"), loved_key("beatles", "hey jude"))
        self.assertEqual(loved_key("Beyoncé", "Halo"), loved_key("beyonce", "halo!"))

    def test_distinct_stay_distinct_and_none_is_empty(self):
        self.assertNotEqual(loved_key("Oasis", "Wonderwall"), loved_key("Oasis", "Supersonic"))
        self.assertEqual(loved_key(None, None), ("", ""))
        self.assertEqual(normalise_name("The Beatles"), "beatles")


class MarkLovedTests(unittest.TestCase):
    def _tracks(self):
        return [
            {"id": "1", "artist": "The Beatles", "title": "Hey Jude", "mbid": "REC-1"},
            {"id": "2", "artist": "Oasis", "title": "Wonderwall"},
        ]

    def test_name_fallback_when_no_mbids(self):
        tracks = self._tracks()
        self.assertEqual(mark_loved(tracks, {loved_key("beatles", "hey jude")}), 1)
        self.assertTrue(tracks[0]["loved"])
        self.assertNotIn("loved", tracks[1])

    def test_mbid_match_beats_differing_titles(self):
        tracks = self._tracks()
        tracks[0]["title"] = "Hey Jude (Remastered 2015)"  # names no longer fold equal
        self.assertEqual(mark_loved(tracks, set(), {"rec-1"}), 1)  # case-insensitive
        self.assertTrue(tracks[0]["loved"])

    def test_track_without_mbid_still_matches_by_name_when_mbids_given(self):
        tracks = self._tracks()
        self.assertEqual(mark_loved(tracks, {loved_key("Oasis", "Wonderwall")}, {"zzz"}), 1)
        self.assertTrue(tracks[1]["loved"])

    def test_empty_inputs_are_a_noop(self):
        tracks = self._tracks()
        self.assertEqual(mark_loved(tracks, set()), 0)
        self.assertEqual(mark_loved(tracks, set(), set()), 0)
        self.assertNotIn("loved", tracks[0])

    def test_already_loved_not_recounted(self):
        tracks = self._tracks()
        tracks[0]["loved"] = True
        self.assertEqual(mark_loved(tracks, set(), {"rec-1"}), 0)


class FakeLB:
    def __init__(self, rows=None, enabled=True, raises=False):
        self.rows, self.enabled, self.raises = rows or [], enabled, raises

    async def loved_tracks(self):
        if self.raises:
            raise RuntimeError("down")
        return list(self.rows)


class ApplyLovedSignalTests(unittest.TestCase):
    def _run(self, client, tracks):
        with mock.patch.object(main, "get_listenbrainz_client", return_value=client):
            asyncio.run(main.apply_loved_signal(tracks))

    def test_boosts_by_mbid_and_name(self):
        tracks = [
            {"id": "1", "artist": "A", "title": "One", "mbid": "r1"},
            {"id": "2", "artist": "The B", "title": "Two"},
            {"id": "3", "artist": "C", "title": "Three"},
        ]
        rows = [
            {"artist": "x", "title": "y", "mbid": "R1", "artist_mbid": ""},
            {"artist": "B", "title": "Two", "mbid": "", "artist_mbid": ""},
        ]
        self._run(FakeLB(rows), tracks)
        self.assertEqual([bool(t.get("loved")) for t in tracks], [True, True, False])

    def test_unconfigured_leaves_tracks_alone(self):
        tracks = [{"id": "1", "artist": "A", "title": "One"}]
        self._run(FakeLB([{"artist": "A", "title": "One", "mbid": ""}], enabled=False), tracks)
        self.assertNotIn("loved", tracks[0])

    def test_empty_loved_list_is_fine(self):
        tracks = [{"id": "1", "artist": "A", "title": "One"}]
        self._run(FakeLB([]), tracks)
        self.assertNotIn("loved", tracks[0])


if __name__ == "__main__":
    unittest.main()
