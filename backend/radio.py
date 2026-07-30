"""
Radio playlist generation.

Builds a "station" seeded from an artist or a song by pooling candidate tracks
from the listener's own library — the seed artist plus similar-style artists (and
a genre-based fallback) — which an AI curator then narrows to a coherent,
similar-style mix. The AI additionally suggests albums by fitting artists that are
NOT present in the library.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("scheduler")

# Cap the candidate pool before smart-filtering to keep payloads/latency sane.
MAX_CANDIDATE_TRACKS = 800
# How many similar artists to pull tracks from.
MAX_SIMILAR_ARTISTS = 12


class RadioProcessor:
    """Resolves a radio seed and gathers similar-style candidate tracks."""

    def __init__(self, nav_client):
        self.nav_client = nav_client

    async def resolve_seed(
        self,
        seed_type: str,
        seed_id: str,
        library_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Resolve a seed (artist or song) into a normalised descriptor.

        Returns a dict with: type, id, name (display label), artist_id, artist_name,
        genre, song_title (for song seeds).
        """
        if seed_type == "song":
            song = await self.nav_client.get_song(seed_id)
            artist_name = song.get("artist") or "Unknown Artist"
            title = song.get("title") or "Unknown"
            return {
                "type": "song",
                "id": seed_id,
                "name": f"{title} — {artist_name}",
                "song_title": title,
                "artist_id": song.get("artist_id"),
                "artist_name": artist_name,
                "genre": song.get("genre")
            }

        # Default: artist seed
        artists = await self.nav_client.get_artists(library_ids)
        artist = next((a for a in artists if a["id"] == seed_id), None)
        if not artist:
            raise Exception("Artist not found")
        return {
            "type": "artist",
            "id": seed_id,
            "name": artist["name"],
            "artist_id": seed_id,
            "artist_name": artist["name"],
            "genre": None
        }

    async def gather_candidate_tracks(
        self,
        seed: Dict[str, Any],
        library_ids: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Build a de-duplicated candidate pool of similar-style tracks from the library.

        Strategy (each step only runs while the pool is still thin, so a single
        good source short-circuits the rest):
          1. Similar songs via Subsonic getSimilarSongs2 — a Last.fm-backed,
             library-resident pool of the seed artist + similar artists in one call.
          2. The seed artist's own tracks (guarantees the seed is represented).
          3. Backfill from similar artists' catalogues.
          4. Fall back to the seed's primary genre.
        """
        candidates: List[Dict[str, Any]] = []
        seen_ids = set()

        def add_tracks(tracks: List[Dict[str, Any]]):
            for track in tracks:
                tid = track.get("id")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    candidates.append(track)

        artist_id = seed.get("artist_id")
        artist_name = seed.get("artist_name")

        # 1. Similar songs (getSimilarSongs2) — the primary, song-level recall.
        if artist_id:
            try:
                similar_songs = await self.nav_client.get_similar_songs(artist_id, MAX_CANDIDATE_TRACKS)
                add_tracks(similar_songs)
                logger.info(f"📻 Radio: {len(similar_songs)} similar songs for seed '{artist_name}'")
            except Exception as e:
                logger.warning(f"⚠️ Radio: failed to fetch similar songs: {e}")

        # 2. Seed artist tracks — ensure the seed itself is present in the pool.
        if artist_id:
            try:
                seed_tracks = await self.nav_client.get_tracks_by_artist(artist_id, library_ids)
                add_tracks(seed_tracks)
                logger.info(f"📻 Radio: {len(seed_tracks)} tracks from seed artist '{artist_name}'")
            except Exception as e:
                logger.warning(f"⚠️ Radio: failed to fetch seed artist tracks: {e}")

        # 3. Backfill from similar artists — only when the pool is still thin.
        similar_artists = []
        if artist_id and len(candidates) < max(50, MAX_SIMILAR_ARTISTS):
            try:
                similar_artists = await self.nav_client.get_similar_artists(artist_id, MAX_SIMILAR_ARTISTS)
            except Exception as e:
                logger.warning(f"⚠️ Radio: failed to fetch similar artists: {e}")

        for similar in similar_artists[:MAX_SIMILAR_ARTISTS]:
            if len(candidates) >= MAX_CANDIDATE_TRACKS:
                break
            try:
                sim_tracks = await self.nav_client.get_tracks_by_artist(similar["id"], library_ids)
                add_tracks(sim_tracks)
            except Exception as e:
                logger.warning(f"⚠️ Radio: failed to fetch tracks for similar artist {similar.get('name')}: {e}")

        # 4. Genre fallback — broaden the pool when we have little to work with
        derived_genre = seed.get("genre") or self._most_common_genre(candidates)
        if len(candidates) < max(50, MAX_SIMILAR_ARTISTS) and derived_genre:
            try:
                logger.info(f"📻 Radio: broadening pool via genre '{derived_genre}'")
                genre_tracks = await self.nav_client.get_tracks_by_genre(derived_genre, library_ids)
                add_tracks(genre_tracks[:MAX_CANDIDATE_TRACKS])
            except Exception as e:
                logger.warning(f"⚠️ Radio: genre fallback failed: {e}")

        logger.info(
            f"📻 Radio: assembled {len(candidates)} candidate tracks "
            f"({len(similar_artists)} similar artists) for seed '{seed.get('name')}'"
        )
        return candidates[:MAX_CANDIDATE_TRACKS]

    @staticmethod
    def _most_common_genre(tracks: List[Dict[str, Any]]) -> Optional[str]:
        """Return the most frequent genre across a set of tracks, if any."""
        counts: Dict[str, int] = {}
        for track in tracks:
            genre = track.get("genre")
            if genre:
                counts[genre] = counts.get(genre, 0) + 1
        if not counts:
            return None
        return max(counts.items(), key=lambda kv: kv[1])[0]
