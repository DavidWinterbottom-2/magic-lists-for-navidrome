"""
ListenBrainz client (public reads, username only).

Replaces the former Last.fm integration. Three signals are recovered from
ListenBrainz that Navidrome does not expose:

  * the listener's loved tracks   (feedback API, score=1)
  * the listener's top recordings (stats API, ~6 months)
  * artists similar to a seed     (LB Labs, keyed by artist MBID)

It is OPTIONAL: with no LISTENBRAINZ_USERNAME every method returns empty, so all
callers degrade to their Navidrome-only behaviour. No token is read or needed.

Failure policy: any error, timeout, non-JSON body or HTTP 204 (stats not computed
yet) yields an empty result and a logged warning, never an exception. Empty
results produced that way are NOT cached, so the first scrobbles/loves show up on
the next build instead of being hidden for hours.

Network and pure parse helpers are kept apart so parsing is fixture-testable.
Response shapes: loved-track feedback and Labs similar artists were confirmed by a
live probe on 2026-11-22 (issue #65); the 200 body of the top-recordings endpoint
follows the documented format and is not yet confirmed (the probe account only had
a 204), so its parser is deliberately tolerant.
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from .errors import describe_exception

logger = logging.getLogger("scheduler")

DEFAULT_API_ROOT = "https://api.listenbrainz.org"
LABS_API_ROOT = "https://labs.api.listenbrainz.org"
# Similar-artist algorithm used by LB Labs (the one LB Radio uses).
SIMILAR_ALGORITHM = "session_based_days_9000_session_300_contribution_5_threshold_15_limit_50_skip_30"

LOVED_CACHE_TTL_SECONDS = 6 * 60 * 60
LOVED_PAGE_SIZE = 100
LOVED_MAX_PAGES = 10
SIMILAR_ARTIST_LIMIT = 40


def parse_loved_tracks(data: Any) -> List[Dict[str, str]]:
    """Extract {artist, title, mbid, artist_mbid} rows from a get-feedback payload.

    `mbid` is the recording MBID (top-level `recording_mbid`, else the mapped one).
    Entries lacking any usable identity are skipped; `artist_mbid` may be empty.
    """
    if not isinstance(data, dict):
        return []
    feedback = data.get("feedback")
    if not isinstance(feedback, list):
        return []

    rows: List[Dict[str, str]] = []
    for entry in feedback:
        if not isinstance(entry, dict):
            continue
        meta = entry.get("track_metadata") or {}
        mapping = meta.get("mbid_mapping") or {}
        mbid = entry.get("recording_mbid") or mapping.get("recording_mbid") or ""
        artist = meta.get("artist_name") or ""
        title = meta.get("track_name") or ""
        artist_mbids = mapping.get("artist_mbids") or []
        artist_mbid = artist_mbids[0] if artist_mbids else ""
        if not mbid and not (artist and title):
            continue
        rows.append({"artist": artist, "title": title, "mbid": mbid, "artist_mbid": artist_mbid})
    return rows


def parse_top_tracks(data: Any) -> List[Dict[str, str]]:
    """Extract {artist, title, mbid, playcount} rows from a stats/recordings payload.

    Rows keep their ranked order (most-listened first). Tolerates the payload
    being absent or shaped unexpectedly by returning [].
    """
    if not isinstance(data, dict):
        return []
    payload = data.get("payload")
    recordings = payload.get("recordings") if isinstance(payload, dict) else None
    if not isinstance(recordings, list):
        return []

    rows: List[Dict[str, str]] = []
    for entry in recordings:
        if not isinstance(entry, dict):
            continue
        title = (entry.get("track_name") or "").strip()
        if not title:
            continue
        rows.append({
            "artist": entry.get("artist_name") or "",
            "title": title,
            "mbid": entry.get("recording_mbid") or "",
            "playcount": str(entry.get("listen_count") or ""),
        })
    return rows


def parse_similar_artists(data: Any) -> List[Dict[str, Any]]:
    """Extract {name, mbid, match} rows from a Labs similar-artists payload.

    Rows are ranked by Labs' integer `score`, highest first (the seed itself and
    nameless rows are dropped). `match` is the score as a string.
    """
    if not isinstance(data, list):
        return []
    rows: List[Dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").strip()
        mbid = entry.get("artist_mbid") or ""
        if not name or mbid == entry.get("reference_mbid"):
            continue
        score = entry.get("score")
        rows.append({
            "name": name,
            "mbid": mbid,
            "match": str(score) if score is not None else "",
            "_score": score if isinstance(score, (int, float)) else 0,
        })
    rows.sort(key=lambda r: r["_score"], reverse=True)
    for r in rows:
        del r["_score"]
    return rows


class ListenBrainzClient:
    """Thin async wrapper over ListenBrainz public APIs. No-op without a username."""

    def __init__(self):
        self.username = (os.getenv("LISTENBRAINZ_USERNAME") or "").strip()
        self.api_root = (os.getenv("LISTENBRAINZ_URL") or DEFAULT_API_ROOT).strip().rstrip("/")
        self.labs_root = LABS_API_ROOT
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        self._loved_cache: Optional[tuple] = None  # (rows, expires_at), non-empty only

    @property
    def enabled(self) -> bool:
        return bool(self.username)

    @property
    def user_enabled(self) -> bool:
        return bool(self.username)

    async def _get(self, url: str, params: Optional[Dict[str, Any]] = None, label: str = "") -> Optional[Any]:
        """GET one endpoint; JSON on 200, None on 204/any error (logged)."""
        try:
            response = await self.client.get(url, params=params)
            if response.status_code == 204:
                logger.info(f"ListenBrainz {label}: no content (204)")
                return None
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.warning(f"⚠️ ListenBrainz {label} HTTP {e.response.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ ListenBrainz {label} failed: {describe_exception(e)}")
        return None

    async def loved_tracks(self) -> List[Dict[str, str]]:
        """The listener's loved tracks as parsed rows ([] when unconfigured/failed)."""
        if not self.user_enabled:
            return []
        if self._loved_cache and self._loved_cache[1] > time.monotonic():
            return self._loved_cache[0]

        rows: List[Dict[str, str]] = []
        offset = 0
        for _ in range(LOVED_MAX_PAGES):
            data = await self._get(
                f"{self.api_root}/1/feedback/user/{self.username}/get-feedback",
                {"score": 1, "count": LOVED_PAGE_SIZE, "offset": offset, "metadata": "true"},
                "get-feedback",
            )
            page = parse_loved_tracks(data)
            if not page:
                break
            rows.extend(page)
            offset += LOVED_PAGE_SIZE
            total = data.get("total_count") if isinstance(data, dict) else None
            if len(page) < LOVED_PAGE_SIZE or (isinstance(total, int) and offset >= total):
                break

        if rows:
            logger.info(f"❤️ ListenBrainz: {len(rows)} loved track(s) for '{self.username}'")
            self._loved_cache = (rows, time.monotonic() + LOVED_CACHE_TTL_SECONDS)
        return rows

    async def top_tracks(self, range_: str = "half_yearly", limit: int = 200) -> List[Dict[str, str]]:
        """The listener's top recordings, most-listened first ([] on 204/error)."""
        if not self.user_enabled:
            return []
        data = await self._get(
            f"{self.api_root}/1/stats/user/{self.username}/recordings",
            {"range": range_, "count": limit},
            "top-recordings",
        )
        return parse_top_tracks(data)

    async def similar_artists(self, artist_mbid: str, limit: int = SIMILAR_ARTIST_LIMIT) -> List[Dict[str, Any]]:
        """Artists similar to the seed MBID, ranked most-similar first."""
        if not self.enabled or not (artist_mbid or "").strip():
            return []
        data = await self._get(
            f"{self.labs_root}/similar-artists/json",
            {"artist_mbids": artist_mbid.strip(), "algorithm": SIMILAR_ALGORITHM},
            "similar-artists",
        )
        return parse_similar_artists(data)[:limit]

    async def listen_count(self) -> Optional[int]:
        """Total listens for the user; None if unknown. Used by the health check."""
        if not self.username:
            return None
        data = await self._get(f"{self.api_root}/1/user/{self.username}/listen-count", None, "listen-count")
        try:
            return int(data["payload"]["count"])
        except (TypeError, KeyError, ValueError):
            return None
