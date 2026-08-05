"""
Last.fm API client.

The app already benefits from Last.fm *indirectly* — Navidrome's getSimilarSongs2
and getArtistInfo2 agents are Last.fm-backed — but Navidrome only ever returns
suggestions that already exist in the local library, and it exposes none of the
listener's own Last.fm history. This client talks to the Last.fm API directly to
recover those signals.

It is an OPTIONAL feature: with no LASTFM_API_KEY configured every method is a
no-op that returns empty, so all callers degrade to their existing behaviour.

Auth model: public-read. Last.fm's user.* history methods (loved/top tracks) are
readable with just an API key + the listener's username, provided that profile
isn't set to hide its listening — no OAuth session key is required. See the
LASTFM_* block in .env.example.

This module deliberately keeps the network call and the pure data-shaping helpers
(`loved_key`, `mark_loved`, `parse_loved_tracks`) separate, so the shaping logic
is unit-testable without a live API.
"""

import logging
import os
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from .errors import describe_exception

logger = logging.getLogger("scheduler")

LASTFM_API_ROOT = "https://ws.audioscrobbler.com/2.0/"
# Loved tracks change rarely; cache the lookup in-process so repeated playlist
# builds within a session don't re-hit the API. Keyed off the singleton client.
LOVED_CACHE_TTL_SECONDS = 6 * 60 * 60
# Last.fm allows up to 1000 results per page; one page is plenty for matching.
LOVED_PAGE_LIMIT = 1000
# How many ranked similar artists to request for album-suggestion grounding.
SIMILAR_ARTIST_LIMIT = 40

_LEADING_ARTICLE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalise(value: Optional[str]) -> str:
    """Fold a title/artist to a match key: lowercase, no articles, alnum only.

    Last.fm's spelling of an artist rarely matches a library's tag character for
    character ("The Beatles" vs "Beatles", "Beyoncé" vs "Beyonce"), so both sides
    are folded to the same shape before comparison. This is intentionally lossy —
    it trades a few false positives for catching the common tagging differences.
    """
    text = (value or "").strip().lower()
    text = _LEADING_ARTICLE.sub("", text)
    # Fold accents to their base letter ("beyoncé" → "beyonce") before dropping
    # the remaining punctuation, so the accented and unaccented tags still match.
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return _NON_ALNUM.sub("", text)


def loved_key(artist: Optional[str], title: Optional[str]) -> Tuple[str, str]:
    """A normalised (artist, title) pair used to match tracks across sources."""
    return (_normalise(artist), _normalise(title))


def normalise_name(name: Optional[str]) -> str:
    """Fold an artist name to a match key (see `_normalise`).

    Exposed so callers can test a Last.fm artist name against the local library
    with the same case/article/accent folding used for loved-track matching.
    """
    return _normalise(name)


def parse_similar_artists(data: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract {name, mbid, match} rows from an artist.getSimilar payload.

    `match` is Last.fm's 0–1 similarity score as a string; rows arrive already
    ranked most-similar first, and that order is preserved here. As elsewhere the
    `artist` list may be a bare object for a single result or absent for none.
    """
    similar = data.get("similarartists", {}).get("artist", [])
    if isinstance(similar, dict):
        similar = [similar]

    rows: List[Dict[str, str]] = []
    for entry in similar:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        rows.append({
            "name": name,
            "mbid": entry.get("mbid") or "",
            "match": str(entry.get("match") or ""),
        })
    return rows


def parse_loved_tracks(data: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract {artist, title, mbid} rows from a user.getLovedTracks payload.

    The API returns `track` as a list normally, a bare object for a single loved
    track, and omits it entirely for none — all three are handled here.
    """
    loved = data.get("lovedtracks", {}).get("track", [])
    if isinstance(loved, dict):
        loved = [loved]

    rows: List[Dict[str, str]] = []
    for entry in loved:
        artist = entry.get("artist", {})
        artist_name = artist.get("name") if isinstance(artist, dict) else artist
        rows.append({
            "artist": artist_name or "",
            "title": entry.get("name") or "",
            "mbid": entry.get("mbid") or "",
        })
    return rows


def parse_top_tracks(data: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract {artist, title, playcount} rows from a user.getTopTracks payload.

    Rows arrive ranked most-played first, and that order is preserved. As with the
    other endpoints, `track` may be a list, a bare object, or absent.
    """
    tracks = data.get("toptracks", {}).get("track", [])
    if isinstance(tracks, dict):
        tracks = [tracks]

    rows: List[Dict[str, str]] = []
    for entry in tracks:
        artist = entry.get("artist", {})
        artist_name = artist.get("name") if isinstance(artist, dict) else artist
        title = (entry.get("name") or "").strip()
        if not title:
            continue
        rows.append({
            "artist": artist_name or "",
            "title": title,
            "playcount": str(entry.get("playcount") or ""),
        })
    return rows


def mark_loved(tracks: List[Dict[str, Any]], loved_keys: Set[Tuple[str, str]]) -> int:
    """Flag tracks whose (artist, title) is in the loved set. Returns the count.

    Sets `track['loved'] = True`, the exact signal `score_tracks_by_user_engagement`
    already rewards (+50) but which nothing else in the app populates.
    """
    if not loved_keys:
        return 0
    marked = 0
    for track in tracks:
        if track.get("loved"):
            continue  # already loved (e.g. via a Navidrome star) — don't recount
        key = loved_key(track.get("artist"), track.get("title"))
        if key in loved_keys:
            track["loved"] = True
            marked += 1
    return marked


class LastfmClient:
    """Thin async wrapper over the Last.fm API. No-op unless an API key is set."""

    def __init__(self):
        self.api_key = (os.getenv("LASTFM_API_KEY") or "").strip()
        self.username = (os.getenv("LASTFM_USERNAME") or "").strip()
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        # (keys, expires_at) — an empty result is cached too, so a private profile
        # doesn't trigger a lookup on every build.
        self._loved_cache: Optional[Tuple[Set[Tuple[str, str]], float]] = None

    @property
    def enabled(self) -> bool:
        """True when Last.fm's global (keyless-user) methods can be called."""
        return bool(self.api_key)

    @property
    def user_enabled(self) -> bool:
        """True when the listener's own history (loved/top tracks) can be read."""
        return bool(self.api_key and self.username)

    async def _get(self, method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Call one Last.fm API method, returning the parsed JSON or None on error.

        Every failure is swallowed and logged: Last.fm is an enrichment source, so
        a bad key or an outage must never take down a playlist build.
        """
        if not self.api_key:
            return None
        query = {
            "method": method,
            "api_key": self.api_key,
            "format": "json",
            **params,
        }
        try:
            response = await self.client.get(LASTFM_API_ROOT, params=query)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and data.get("error"):
                logger.warning(
                    f"⚠️ Last.fm {method} error {data.get('error')}: {data.get('message')}"
                )
                return None
            return data
        except httpx.HTTPStatusError as e:
            logger.warning(f"⚠️ Last.fm {method} HTTP {e.response.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ Last.fm {method} failed: {describe_exception(e)}")
        return None

    async def loved_track_keys(self) -> Set[Tuple[str, str]]:
        """The listener's loved tracks as a set of normalised (artist, title) keys.

        Returns an empty set when Last.fm isn't configured for user reads, the
        profile hides its data, or the call fails — callers treat all three the
        same (no loved signal applied).
        """
        if not self.user_enabled:
            return set()

        if self._loved_cache and self._loved_cache[1] > time.monotonic():
            return self._loved_cache[0]

        data = await self._get("user.getLovedTracks", {
            "user": self.username,
            "limit": LOVED_PAGE_LIMIT,
        })
        keys: Set[Tuple[str, str]] = set()
        if data:
            for row in parse_loved_tracks(data):
                keys.add(loved_key(row["artist"], row["title"]))
            logger.info(f"❤️ Last.fm: {len(keys)} loved track(s) for '{self.username}'")

        self._loved_cache = (keys, time.monotonic() + LOVED_CACHE_TTL_SECONDS)
        return keys

    async def similar_artists(self, artist_name: str, limit: int = SIMILAR_ARTIST_LIMIT) -> List[Dict[str, str]]:
        """Artists similar to `artist_name`, ranked most-similar first.

        Unlike Navidrome's getArtistInfo2 — which drops every suggestion not in
        the local library — this returns Last.fm's full ranked list, including the
        out-of-library artists that make good album suggestions. Needs only the
        API key (no username). Returns [] when unconfigured or on any failure.
        """
        if not self.enabled or not (artist_name or "").strip():
            return []
        data = await self._get("artist.getSimilar", {
            "artist": artist_name,
            "limit": limit,
            "autocorrect": 1,
        })
        return parse_similar_artists(data) if data else []

    async def top_tracks(self, period: str = "6month", limit: int = 200) -> List[Dict[str, str]]:
        """The listener's most-played tracks over `period`, ranked most-played first.

        This is real cross-player listening history — the plays the listener made
        anywhere that scrobbles, not just through Navidrome — which makes it a
        strong "loved this once, drifted away" source for Re-Discover when the
        in-app play history is thin. `period` is a Last.fm range (7day, 1month,
        3month, 6month, 12month, overall). Returns [] when unconfigured or on error.
        """
        if not self.user_enabled:
            return []
        data = await self._get("user.getTopTracks", {
            "user": self.username,
            "period": period,
            "limit": limit,
        })
        return parse_top_tracks(data) if data else []
