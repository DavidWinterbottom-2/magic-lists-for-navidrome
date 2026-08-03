"""Tests for how playlist tracks are stored and presented.

Playlists used to store a bare list of title strings. They now store
title/artist/album per track so Manage Playlists can show what a track actually
is — but rows written before that change are still in the database, so every
read path has to cope with both shapes.

Run from the repo root:
    python -m unittest tests.test_playlist_tracks
"""

import unittest

from backend.main import normalise_stored_songs, song_labels, summarise_track, summarise_tracks
from backend.schemas import Playlist


def _track(tid, title="Robots", artist="Dan Mangan", album="Nice, Nice, Very Nice"):
    return {"id": tid, "title": title, "artist": artist, "album": album}


class SummariseTrackTests(unittest.TestCase):

    def test_keeps_title_artist_and_album(self):
        self.assertEqual(
            summarise_track(_track("t1")),
            {"title": "Robots", "artist": "Dan Mangan", "album": "Nice, Nice, Very Nice"},
        )

    def test_missing_fields_become_empty_strings(self):
        self.assertEqual(
            summarise_track({"id": "t1", "title": "Solo"}),
            {"title": "Solo", "artist": "", "album": ""},
        )

    def test_none_fields_become_empty_strings(self):
        # Navidrome returns null rather than omitting the key for untagged files
        self.assertEqual(
            summarise_track({"id": "t1", "title": "Solo", "artist": None, "album": None}),
            {"title": "Solo", "artist": "", "album": ""},
        )

    def test_missing_title_falls_back(self):
        self.assertEqual(summarise_track({"id": "t1"})["title"], "Unknown")

    def test_only_the_display_fields_are_stored(self):
        # Storing the whole track would bloat every row with ids and play counts
        summary = summarise_track({**_track("t1"), "play_count": 99, "genre": "Alt"})
        self.assertEqual(set(summary), {"title", "artist", "album"})


class SummariseTracksTests(unittest.TestCase):

    def test_without_order_keeps_the_given_sequence(self):
        tracks = [_track("t1", title="One"), _track("t2", title="Two")]
        self.assertEqual([s["title"] for s in summarise_tracks(tracks)], ["One", "Two"])

    def test_order_reorders_to_the_curated_sequence(self):
        tracks = [_track("t1", title="One"), _track("t2", title="Two"), _track("t3", title="Three")]
        summaries = summarise_tracks(tracks, order=["t3", "t1"])
        self.assertEqual([s["title"] for s in summaries], ["Three", "One"])

    def test_ids_not_present_in_tracks_are_skipped(self):
        # The AI can reference an index that maps to a track outside the pool
        tracks = [_track("t1", title="One")]
        summaries = summarise_tracks(tracks, order=["t1", "missing"])
        self.assertEqual([s["title"] for s in summaries], ["One"])

    def test_empty_inputs(self):
        self.assertEqual(summarise_tracks([]), [])
        self.assertEqual(summarise_tracks([], order=["t1"]), [])


class NormaliseStoredSongsTests(unittest.TestCase):
    """Legacy rows are widened on read rather than migrated."""

    def test_legacy_title_strings_are_widened(self):
        self.assertEqual(
            normalise_stored_songs(["Old Title"]),
            [{"title": "Old Title", "artist": "", "album": ""}],
        )

    def test_new_dict_rows_pass_through(self):
        stored = [{"title": "Robots", "artist": "Dan Mangan", "album": "Oh Fortune"}]
        self.assertEqual(normalise_stored_songs(stored), stored)

    def test_partial_dicts_are_filled_in(self):
        self.assertEqual(
            normalise_stored_songs([{"title": "Robots"}]),
            [{"title": "Robots", "artist": "", "album": ""}],
        )

    def test_mixed_row_is_handled(self):
        # A playlist refreshed after the change can hold both shapes
        result = normalise_stored_songs(["Legacy", {"title": "New", "artist": "A", "album": "B"}])
        self.assertEqual([s["title"] for s in result], ["Legacy", "New"])
        self.assertEqual(result[0]["artist"], "")
        self.assertEqual(result[1]["artist"], "A")

    def test_none_and_empty(self):
        self.assertEqual(normalise_stored_songs(None), [])
        self.assertEqual(normalise_stored_songs([]), [])


class SongLabelTests(unittest.TestCase):
    """Refresh prompts interpolate the previous tracklist as flat strings."""

    def test_new_rows_label_with_the_artist(self):
        stored = [{"title": "Robots", "artist": "Dan Mangan", "album": "X"}]
        self.assertEqual(song_labels(stored), ["Robots — Dan Mangan"])

    def test_legacy_rows_label_with_the_title_alone(self):
        self.assertEqual(song_labels(["Old Title"]), ["Old Title"])

    def test_labels_are_joinable(self):
        # The regression this guards: joining raw dicts raises TypeError, which
        # would break every scheduled refresh.
        stored = [{"title": "A", "artist": "X", "album": ""}, "Legacy"]
        self.assertEqual(", ".join(song_labels(stored)), "A — X, Legacy")


class PlaylistSchemaTests(unittest.TestCase):
    """The stored-playlist model must validate both eras of row."""

    def _playlist(self, songs):
        return Playlist(id=1, artist_id="A1", playlist_name="P", songs=songs,
                        created_at="2026-01-01", updated_at="2026-01-01")

    def test_accepts_new_track_dicts(self):
        playlist = self._playlist([{"title": "Robots", "artist": "Dan Mangan", "album": "X"}])
        self.assertEqual(playlist.model_dump()["songs"][0]["artist"], "Dan Mangan")

    def test_accepts_legacy_title_strings(self):
        playlist = self._playlist(["Old Title"])
        self.assertEqual(playlist.model_dump()["songs"], ["Old Title"])

    def test_accepts_a_mixed_list(self):
        playlist = self._playlist(["Old", {"title": "New", "artist": "A", "album": "B"}])
        self.assertEqual(len(playlist.model_dump()["songs"]), 2)


if __name__ == "__main__":
    unittest.main()
