"""
ListenBrainz client (public reads, username only).

Three signals are recovered from
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
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

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
# Stats endpoints serve at most 100 rows per request; larger counts are paged.
STATS_PAGE_SIZE = 100
SIMILAR_ARTIST_LIMIT = 40
# Labs artist names are community-edited and end up in the curator's prompt:
# keep them short and on one line.
MAX_ARTIST_NAME_LENGTH = 100
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def _clean_name(value: Any) -> str:
    """One-line, length-capped artist name (prompt-safe)."""
    return _CONTROL_CHARS.sub(" ", str(value or "")).strip()[:MAX_ARTIST_NAME_LENGTH].strip()


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
    nameless rows are dropped). `match` is the score as a string. Names are
    flattened to one line and capped at MAX_ARTIST_NAME_LENGTH characters, since
    they are fed to the AI curator.
    """
    if not isinstance(data, list):
        return []
    rows: List[Dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = _clean_name(entry.get("name"))
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
    def _user(self) -> str:
        """Username as one URL path segment (`/`, `?`, `#` and `..` can't escape it)."""
        return quote(self.username, safe="")

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
        if not self.enabled:
            return []
        if self._loved_cache and self._loved_cache[1] > time.monotonic():
            return list(self._loved_cache[0])

        rows: List[Dict[str, str]] = []
        offset = 0
        complete = True
        for _ in range(LOVED_MAX_PAGES):
            data = await self._get(
                f"{self.api_root}/1/feedback/user/{self._user}/get-feedback",
                {"score": 1, "count": LOVED_PAGE_SIZE, "offset": offset, "metadata": "true"},
                "get-feedback",
            )
            if data is None and offset:
                complete = False  # a later page failed: rows are partial
                break
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
            if complete:  # partial results are used once, not cached for hours
                self._loved_cache = (list(rows), time.monotonic() + LOVED_CACHE_TTL_SECONDS)
        return rows

    async def top_tracks(self, range_: str = "half_yearly", limit: int = 200) -> List[Dict[str, str]]:
        """The listener's top recordings, most-listened first ([] on 204/error).

        Fetched in pages of STATS_PAGE_SIZE (the server's per-request cap) up to
        `limit`; a failed later page keeps the rows already read.
        """
        if not self.enabled:
            return []
        rows: List[Dict[str, str]] = []
        while len(rows) < limit:
            count = min(STATS_PAGE_SIZE, limit - len(rows))
            data = await self._get(
                f"{self.api_root}/1/stats/user/{self._user}/recordings",
                {"range": range_, "count": count, "offset": len(rows)},
                "top-recordings",
            )
            page = parse_top_tracks(data)
            rows.extend(page)
            if len(page) < count:
                break
        return rows[:limit]

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
