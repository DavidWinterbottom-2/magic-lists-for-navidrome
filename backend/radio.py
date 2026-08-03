"""
Radio playlist generation.

Builds a "station" seeded from an artist or a song by pooling candidate tracks
from the listener's own library — the seed artist plus similar-style artists (and
a genre-based fallback) — which an AI curator then narrows to a coherent,
similar-style mix. The AI additionally suggests albums by fitting artists that are
NOT present in the library.
"""

import logging
import math
import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from .errors import describe_exception

logger = logging.getLogger("scheduler")

# Cap the candidate pool before smart-filtering to keep payloads/latency sane.
MAX_CANDIDATE_TRACKS = 800
# How many similar artists to pull tracks from.
MAX_SIMILAR_ARTISTS = 12
# Only reach for the artist/genre backfill while the pool is still this thin.
MIN_POOL_BEFORE_BACKFILL = max(50, MAX_SIMILAR_ARTISTS)
# Share of the station the seed artist may occupy. A station is meant to be
# "artists like X", not a greatest-hits of X.
SEED_ARTIST_SHARE = 0.2


def lidarr_add_url(
    artist: str,
    album: Optional[str] = None,
    base_url: Optional[str] = None
) -> Optional[str]:
    """Deep link into Lidarr's "Add New" search, prefilled with this release.

    Lidarr's SPA route is /add/search, and that page seeds its search box from
    the `term` query parameter (Lidarr's AddNewItemConnector reads
    `params.term`). The search is MusicBrainz-backed and returns both artist and
    album matches, so passing "<artist> <album>" lands on the album where one
    exists and on the artist otherwise — either is addable from that page.

    Returns None when no Lidarr is configured, so callers can fall back to
    rendering the suggestion as plain text rather than a dead link.
    """
    base = (os.getenv("LIDARR_URL", "") if base_url is None else base_url).strip()
    if not base:
        return None

    term = " ".join(part for part in ((artist or "").strip(), (album or "").strip()) if part)
    if not term:
        return None

    return f"{base.rstrip('/')}/add/search?term={quote_plus(term)}"


def build_shortfall(
    requested: int,
    delivered: int,
    candidate_pool_size: int = 0,
    distinct_artists: int = 0,
    warnings: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Describe the gap between the station that was asked for and the one built.

    A thin library is the normal cause of a short station: there simply isn't
    enough similar-style material to fill the requested length without repeating
    artists. That is worth telling the listener explicitly — silently returning
    12 tracks for a 25-track request reads as a bug, and the gap is exactly what
    the album suggestions below it are there to fix.

    `warnings` carries recall failures from the candidate pool. A full-length
    station can still be a degraded one — if the similar-songs lookup timed out,
    the station was rebuilt from a fallback and will look much like the last one
    however many tracks it has. That needs reporting even when nothing is short.
    """
    missing = max(0, requested - delivered)
    warnings = list(warnings or [])

    if missing == 0:
        return {
            "requested": requested,
            "delivered": delivered,
            "missing": 0,
            "is_short": False,
            "distinct_artists": distinct_artists,
            "warnings": warnings,
            "message": " ".join(warnings),
        }

    if delivered == 0:
        message = (
            "Your library didn't have anything similar enough to build this station."
        )
    else:
        message = (
            f"Only {delivered} of the {requested} tracks you asked for — your library "
            f"ran out of similar-style music to draw on."
        )

    if candidate_pool_size and candidate_pool_size < requested:
        detail = (
            f"Just {candidate_pool_size} candidate track"
            f"{'' if candidate_pool_size == 1 else 's'} matched this seed."
        )
    else:
        detail = "Adding the albums below would give this station more to work with."

    # A recall failure explains the gap far better than "your library is thin",
    # so it leads the message when there is one.
    if warnings:
        detail = " ".join(warnings)

    return {
        "requested": requested,
        "delivered": delivered,
        "missing": missing,
        "is_short": True,
        "distinct_artists": distinct_artists,
        "warnings": warnings,
        "message": f"{message} {detail}",
    }


def artist_key(track: Dict[str, Any]) -> str:
    """Group tracks by artist, preferring the stable id over the display name."""
    return str(
        track.get("artist_id")
        or track.get("artistId")
        or (track.get("artist") or "").strip().lower()
        or "unknown"
    )


def _artist_identity(artist_id: Any, artist_name: Any) -> set:
    """Every way one artist can be identified across Navidrome payload shapes.

    Genre-fallback tracks sometimes arrive without an artist id, so matching on
    the id alone would miss them; matching on either id or lowercased name lets
    a seed and a track recognise each other whichever field is populated.
    """
    keys = set()
    if artist_id:
        keys.add(str(artist_id))
    name = (artist_name or "").strip().lower()
    if name:
        keys.add(name)
    return keys


def promote_seed_first(
    curated: List[Dict[str, Any]],
    seed: Dict[str, Any],
    candidate_pool: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """Open the station with the seed itself.

    A station seeded from "Dan Mangan" should start with Dan Mangan, and one
    seeded from a specific song should start with that song — that is what makes
    it read as *this* artist's radio rather than a generic mix. The curator is
    asked to open with recognisable tracks but isn't told to lead with the seed,
    so the guarantee is enforced here.

    Preference order: the seed song itself, then any track by the seed artist,
    then leave the curator's opener alone. When the curator dropped the seed
    entirely, it is pulled back in from the candidate pool and the last track is
    dropped so the playlist length is unchanged.
    """
    if not curated:
        return curated

    seed_song_id = seed.get("id") if seed.get("type") == "song" else None
    seed_artist = _artist_identity(seed.get("artist_id"), seed.get("artist_name"))

    def rank(track: Dict[str, Any]) -> int:
        if seed_song_id and track.get("id") == seed_song_id:
            return 0
        track_artist = _artist_identity(
            track.get("artist_id") or track.get("artistId"), track.get("artist")
        )
        if seed_artist and track_artist & seed_artist:
            return 1
        return 2

    # Best opener already curated? min() keeps the earliest index among ties,
    # so the curator's own ordering still breaks the tie.
    best = min(range(len(curated)), key=lambda i: rank(curated[i]))
    if rank(curated[best]) < 2:
        return [curated[best]] + curated[:best] + curated[best + 1:]

    # Curator left the seed out — reinstate it from the pool, keeping the length.
    for track in candidate_pool or []:
        if rank(track) < 2:
            return [track] + curated[:len(curated) - 1]

    return curated


def seed_artist_limit(num_tracks: int, share: float = SEED_ARTIST_SHARE) -> int:
    """How many tracks the seed artist may contribute to a station of this size.

    Floored rather than rounded up, so the share is a true ceiling — but never
    below 1, because a station that doesn't play its own seed artist at all
    would contradict `promote_seed_first`.
    """
    return max(1, math.floor(num_tracks * share))


def cap_seed_artist(
    curated: List[Dict[str, Any]],
    seed: Dict[str, Any],
    num_tracks: Optional[int] = None,
    candidate_pool: Optional[List[Dict[str, Any]]] = None,
    share: float = SEED_ARTIST_SHARE
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Hold the seed artist to `share` of the station, backfilling the gap.

    The recipe already asks the model for at most 20% from any one artist, but a
    model can't be relied on for a hard guarantee — and the seed artist is the
    one most likely to run away with the tracklist, since it dominates the
    candidate pool whenever the library is thin on similar artists.

    Excess seed-artist tracks are replaced with other artists from the candidate
    pool rather than simply dropped, so a healthy library still returns a
    full-length station. When the pool has nobody else to offer, the station
    comes back short — which `build_shortfall` then reports to the listener.
    """
    target = num_tracks or len(curated)
    limit = seed_artist_limit(target, share)
    seed_keys = _artist_identity(seed.get("artist_id"), seed.get("artist_name"))

    def is_seed_artist(track: Dict[str, Any]) -> bool:
        track_keys = _artist_identity(
            track.get("artist_id") or track.get("artistId"), track.get("artist")
        )
        return bool(seed_keys and seed_keys & track_keys)

    kept: List[Dict[str, Any]] = []
    used_ids = set()
    seed_kept = 0
    dropped = 0

    for track in curated:
        if is_seed_artist(track):
            if seed_kept >= limit:
                dropped += 1
                continue
            seed_kept += 1
        kept.append(track)
        used_ids.add(track.get("id"))

    # Refill the space the dropped seed tracks left, from anyone but the seed.
    backfilled = 0
    if dropped and candidate_pool:
        for track in candidate_pool:
            if len(kept) >= target:
                break
            track_id = track.get("id")
            if track_id in used_ids or is_seed_artist(track):
                continue
            used_ids.add(track_id)
            kept.append(track)
            backfilled += 1

    return kept, {
        "seed_artist_tracks": seed_kept,
        "seed_artist_limit": limit,
        "dropped_for_seed_cap": dropped,
        "backfilled": backfilled,
    }


def count_distinct_artists(tracks: List[Dict[str, Any]]) -> int:
    """Number of distinct artists represented in a track list."""
    return len({artist_key(t) for t in tracks})


def artists_needed(num_tracks: int, max_per_artist: int) -> int:
    """Distinct artists required to fill num_tracks without exceeding the cap."""
    return math.ceil(num_tracks / max(1, max_per_artist))


def enforce_artist_cap(
    tracks: List[Dict[str, Any]],
    max_per_artist: int,
    num_tracks: Optional[int] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Drop tracks that exceed the per-artist cap, preserving curation order.

    The recipe asks the model to respect the cap, but a model cannot be relied
    on for a hard guarantee — and when the candidate pool is dominated by one
    artist it has nothing else to offer. Enforcing it here means the limit holds
    regardless of what the model returns.

    Deliberately returns a SHORTER playlist rather than padding with extra
    tracks by an already-used artist: a thin library should yield a short
    station, with the gap reported to the listener as albums worth buying.
    """
    kept: List[Dict[str, Any]] = []
    per_artist: Dict[str, int] = {}
    dropped = 0

    for track in tracks:
        key = artist_key(track)
        if per_artist.get(key, 0) >= max_per_artist:
            dropped += 1
            continue
        per_artist[key] = per_artist.get(key, 0) + 1
        kept.append(track)
        if num_tracks and len(kept) >= num_tracks:
            break

    return kept, {
        "dropped_for_cap": dropped,
        "distinct_artists": len(per_artist),
        "max_per_artist": max_per_artist,
    }


class RadioProcessor:
    """Resolves a radio seed and gathers similar-style candidate tracks."""

    def __init__(self, nav_client):
        self.nav_client = nav_client
        # Recall failures that degraded the pool. Each step below is allowed to
        # fail so a station can still be built, but a station built from a
        # fallback is a materially worse station and the listener should be told
        # rather than left wondering why nothing changed.
        self.pool_warnings: List[str] = []

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
        library_ids: Optional[List[str]] = None,
        min_artists: int = 0
    ) -> List[Dict[str, Any]]:
        """Build a de-duplicated candidate pool of similar-style tracks from the library.

        Strategy (each step only runs while the pool is still thin, so a single
        good source short-circuits the rest):
          1. Similar songs via Subsonic getSimilarSongs2 — a Last.fm-backed,
             library-resident pool of the seed artist + similar artists in one call.
          2. The seed artist's own tracks (guarantees the seed is represented).
          3. Backfill from similar artists' catalogues.
          4. Fall back to the seed's primary genre.

        Steps 3 and 4 also trigger when the pool holds enough TRACKS but too few
        distinct ARTISTS (min_artists). A seed artist with a deep catalogue and
        no library-resident neighbours would otherwise satisfy a track-count
        check on its own and yield a single-artist station.
        """
        candidates: List[Dict[str, Any]] = []
        seen_ids = set()
        self.pool_warnings = []

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
                similar_songs = await self.nav_client.get_similar_songs(artist_id, MAX_CANDIDATE_TRACKS, library_ids)
                add_tracks(similar_songs)
                logger.info(f"📻 Radio: {len(similar_songs)} similar songs for seed '{artist_name}'")
            except Exception as e:
                reason = describe_exception(e)
                logger.warning(f"⚠️ Radio: failed to fetch similar songs: {reason}")
                self.pool_warnings.append(
                    "The similar-songs lookup failed "
                    f"({reason}) — this station was built from a reduced pool, "
                    "so it may look much like the last one."
                )

        # 2. Seed artist tracks — ensure the seed itself is present in the pool.
        if artist_id:
            try:
                seed_tracks = await self.nav_client.get_tracks_by_artist(artist_id, library_ids)
                add_tracks(seed_tracks)
                logger.info(f"📻 Radio: {len(seed_tracks)} tracks from seed artist '{artist_name}'")
            except Exception as e:
                reason = describe_exception(e)
                logger.warning(f"⚠️ Radio: failed to fetch seed artist tracks: {reason}")
                self.pool_warnings.append(f"Could not load the seed artist's tracks ({reason}).")

        # 3. Backfill from similar artists — only when the pool is still thin.
        similar_artists = []
        if artist_id and len(candidates) < MIN_POOL_BEFORE_BACKFILL:
            try:
                similar_artists = await self.nav_client.get_similar_artists(artist_id, MAX_SIMILAR_ARTISTS)
            except Exception as e:
                reason = describe_exception(e)
                logger.warning(f"⚠️ Radio: failed to fetch similar artists: {reason}")
                self.pool_warnings.append(f"Could not load similar artists ({reason}).")

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
        if len(candidates) < MIN_POOL_BEFORE_BACKFILL and derived_genre:
            try:
                logger.info(f"📻 Radio: broadening pool via genre '{derived_genre}'")
                genre_tracks = await self.nav_client.get_tracks_by_genre(derived_genre, library_ids)
                add_tracks(genre_tracks[:MAX_CANDIDATE_TRACKS])
            except Exception as e:
                reason = describe_exception(e)
                logger.warning(f"⚠️ Radio: genre fallback failed: {reason}")
                self.pool_warnings.append(f"The genre fallback failed ({reason}).")

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
