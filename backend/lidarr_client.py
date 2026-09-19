"""
Lidarr write client.

Radio's album suggestions point at fitting artists the listener doesn't own yet
(`radio.similar_out_of_library_artists`). Historically the only thing Lidarr
integration did with that was build a deep link into Lidarr's "Add New" search
(`radio.lidarr_add_url`) for the listener to click through and search/add by
hand. This client does the add itself: look the artist up by name to get its
MusicBrainz id, then POST it to Lidarr so it starts monitoring/downloading.

It is OPTIONAL like the Last.fm client: with no LIDARR_API_KEY (on top of the
LIDARR_URL the deep-link feature already needs) plus a quality profile and root
folder configured, `enabled` is False and callers fall back to the existing
deep-link behaviour.
"""

import asyncio
import logging
import os
import re
from typing import Any, Dict, Optional

import httpx

from .errors import describe_exception

logger = logging.getLogger("scheduler")


def build_add_artist_payload(
    lookup_result: Dict[str, Any],
    quality_profile_id: int,
    root_folder_path: str,
    metadata_profile_id: int = 1,
    monitored: bool = True,
    monitor: str = "all",
    search_now: bool = True
) -> Dict[str, Any]:
    """Shape one `artist/lookup` result into an `artist` POST body.

    `monitor` is Lidarr's `addOptions.monitor` enum (all/future/missing/
    existing/first/latest/none) — which albums get monitored at add time,
    distinct from the top-level `monitored` bool (whether the artist itself
    is tracked). Explicit here rather than left to Lidarr's own default, so
    add_album() below can request "none" and monitor just one album itself.

    Pulled out as a pure function so the payload shape is unit-testable without
    a live Lidarr instance.
    """
    return {
        "foreignArtistId": lookup_result["foreignArtistId"],
        "artistName": lookup_result.get("artistName") or lookup_result["foreignArtistId"],
        "qualityProfileId": quality_profile_id,
        "metadataProfileId": metadata_profile_id,
        "rootFolderPath": root_folder_path,
        "monitored": monitored,
        "addOptions": {"monitor": monitor, "searchForMissingAlbums": search_now},
    }


def describe_lidarr_error(exc: Exception) -> str:
    """Render a Lidarr API failure for logs/error messages.

    `describe_exception` stringifies an `httpx.HTTPStatusError` to just its
    status code and URL ("Server error '500 Internal Server Error' for url
    '...'") — that's what httpx puts in the exception message, but it drops
    the actual reason Lidarr gave in the response body (e.g. "Root folder
    does not exist"), which is the one thing worth logging. This surfaces
    that body when there is one, and falls back to `describe_exception` for
    anything else — a non-HTTP failure, or a body that isn't JSON/isn't in
    either shape Lidarr uses (a validation-error array or a plain message).
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return describe_exception(exc)

    response = exc.response
    try:
        data = response.json()
    except Exception:
        text = (response.text or "").strip()
        return text or describe_exception(exc)

    if isinstance(data, list):
        messages = [
            item.get("errorMessage") for item in data
            if isinstance(item, dict) and item.get("errorMessage")
        ]
        if messages:
            return "; ".join(messages)
    if isinstance(data, dict) and data.get("message"):
        return data["message"]

    text = (response.text or "").strip()
    return text or describe_exception(exc)


def pick_lookup_match(results: list, artist_name: str) -> Optional[Dict[str, Any]]:
    """Pick the best `artist/lookup` result for `artist_name`.

    Lidarr's lookup is a MusicBrainz search, not an exact-match API, so it can
    return near-misses ranked by its own relevance. An exact case-insensitive
    name match is preferred when present; otherwise the top-ranked result is
    used, since that is what a listener clicking the top search hit would pick.
    """
    if not results:
        return None
    target = artist_name.strip().lower()
    for result in results:
        if (result.get("artistName") or "").strip().lower() == target:
            return result
    return results[0]


_BRACKETED_SUFFIX = re.compile(r"[\(\[][^)\]]*[\)\]]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalise_album_title(title: Optional[str]) -> str:
    """Fold an album title to a loose match key.

    Lidarr's MusicBrainz-sourced titles often carry a bracketed suffix a
    suggestion's title won't ("Greatest Hits (Remastered)" vs "Greatest
    Hits") — stripped here, along with case and punctuation, before comparing.
    """
    text = (title or "").strip().lower()
    text = _BRACKETED_SUFFIX.sub("", text)
    return _NON_ALNUM.sub("", text)


def pick_album_match(albums: list, album_title: str) -> Optional[Dict[str, Any]]:
    """Match a Lidarr album list entry to a suggested album title.

    Prefers an exact case-insensitive title match; falls back to a normalised
    match (see `normalise_album_title`). Returns None rather than guessing
    when neither matches — monitoring/downloading the wrong album is worse
    than reporting that none was found.
    """
    target = (album_title or "").strip().lower()
    if not target:
        return None
    for album in albums:
        if (album.get("title") or "").strip().lower() == target:
            return album
    normalised_target = normalise_album_title(album_title)
    if normalised_target:
        for album in albums:
            if normalise_album_title(album.get("title")) == normalised_target:
                return album
    return None


class LidarrClient:
    """Thin async wrapper over Lidarr's REST API for adding artists."""

    def __init__(self):
        self.base_url = (os.getenv("LIDARR_URL") or "").strip().rstrip("/")
        self.api_key = (os.getenv("LIDARR_API_KEY") or "").strip()
        self.quality_profile_id = (os.getenv("LIDARR_QUALITY_PROFILE_ID") or "").strip()
        self.root_folder_path = (os.getenv("LIDARR_ROOT_FOLDER_PATH") or "").strip()
        self.metadata_profile_id = (os.getenv("LIDARR_METADATA_PROFILE_ID") or "1").strip()
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        # Overridable in tests so polling in _find_album doesn't actually sleep.
        self._sleep = asyncio.sleep

    @property
    def enabled(self) -> bool:
        """True when there's enough config to add artists via the API.

        Requires all four of URL, API key, quality profile and root folder —
        without a profile/folder Lidarr's add call would 422, so being "half
        configured" degrades to the plain deep-link rather than erroring.
        """
        return bool(
            self.base_url and self.api_key
            and self.quality_profile_id and self.root_folder_path
        )

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        response = await self.client.request(
            method, f"{self.base_url}{path}",
            headers={"X-Api-Key": self.api_key},
            **kwargs
        )
        response.raise_for_status()
        return response.json() if response.content else None

    async def _lookup_artist(self, artist_name: str):
        """GET /api/v1/artist/lookup and pick the best match.

        Returns (match, None) or (None, error_message) — shared by add_artist
        and add_album so both report lookup failures the same way.
        """
        try:
            results = await self._request(
                "GET", "/api/v1/artist/lookup", params={"term": artist_name}
            )
        except Exception as e:
            reason = describe_lidarr_error(e)
            logger.warning(f"⚠️ Lidarr: artist lookup failed for '{artist_name}': {reason}")
            return None, f"Lidarr lookup failed: {reason}"

        match = pick_lookup_match(results or [], artist_name)
        if not match:
            return None, f"No Lidarr match found for '{artist_name}'."
        return match, None

    def _resolve_profile_ids(self):
        """Parse LIDARR_QUALITY_PROFILE_ID/LIDARR_METADATA_PROFILE_ID as ints.

        Returns (quality_id, metadata_id, None) or (None, None, error_message).
        These must be the numeric profile id, not its display name (Lidarr's
        default is literally named "Any", an easy mix-up) — caught here rather
        than left to crash the request, since `enabled` only checks the vars
        are non-empty.
        """
        try:
            return int(self.quality_profile_id), int(self.metadata_profile_id), None
        except ValueError:
            return None, None, (
                "LIDARR_QUALITY_PROFILE_ID/LIDARR_METADATA_PROFILE_ID must be the "
                "numeric profile id (Settings -> Profiles in Lidarr, or GET "
                "/api/v1/qualityprofile), not the profile's name."
            )

    async def add_artist(self, artist_name: str) -> Dict[str, Any]:
        """Look up `artist_name` in Lidarr and add it for monitoring/download.

        Returns {"ok": True, "artist_name": ...} on success, or
        {"ok": False, "error": ...} when the client isn't configured, no match
        was found, or the add itself failed (including "already added").
        """
        if not self.enabled:
            return {"ok": False, "error": "Lidarr isn't fully configured for adding artists."}

        match, error = await self._lookup_artist(artist_name)
        if error:
            return {"ok": False, "error": error}

        quality_profile_id, metadata_profile_id, error = self._resolve_profile_ids()
        if error:
            return {"ok": False, "error": error}

        payload = build_add_artist_payload(
            match,
            quality_profile_id=quality_profile_id,
            root_folder_path=self.root_folder_path,
            metadata_profile_id=metadata_profile_id,
            monitor="all",
            search_now=True,
        )
        try:
            added = await self._request("POST", "/api/v1/artist", json=payload)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                return {"ok": False, "error": f"'{match['artistName']}' is already in Lidarr."}
            reason = describe_lidarr_error(e)
            logger.warning(f"⚠️ Lidarr: add_artist failed for '{artist_name}': {reason}")
            return {"ok": False, "error": f"Lidarr add failed: {reason}"}
        except Exception as e:
            reason = describe_lidarr_error(e)
            logger.warning(f"⚠️ Lidarr: add_artist failed for '{artist_name}': {reason}")
            return {"ok": False, "error": f"Lidarr add failed: {reason}"}

        logger.info(f"📀 Lidarr: added '{added.get('artistName')}' (ID {added.get('id')})")
        return {"ok": True, "artist_name": added.get("artistName") or match["artistName"]}

    async def add_album(self, artist_name: str, album_name: str) -> Dict[str, Any]:
        """Add artist_name to Lidarr with nothing monitored, then monitor and
        search only album_name — the one-album alternative to add_artist(),
        which monitors and searches the artist's whole catalogue.

        Reuses an existing artist entry rather than failing on "already
        added" when the artist is already in Lidarr's library (e.g. from an
        earlier add_artist() click) — the point here is monitoring one more
        album, not the artist itself.

        Returns {"ok": True, "artist_name": ..., "album_title": ...} on full
        success; {"ok": True, "artist_name": ..., "album_title": None,
        "warning": ...} when the artist was added but the album couldn't be
        confirmed/monitored (Lidarr populates an added artist's album list
        asynchronously, so this can still resolve on a later Radio refresh or
        manual check in Lidarr); or {"ok": False, "error": ...} when nothing
        could be added at all.
        """
        if not self.enabled:
            return {"ok": False, "error": "Lidarr isn't fully configured for adding artists."}

        match, error = await self._lookup_artist(artist_name)
        if error:
            return {"ok": False, "error": error}

        quality_profile_id, metadata_profile_id, error = self._resolve_profile_ids()
        if error:
            return {"ok": False, "error": error}

        payload = build_add_artist_payload(
            match,
            quality_profile_id=quality_profile_id,
            root_folder_path=self.root_folder_path,
            metadata_profile_id=metadata_profile_id,
            monitor="none",
            search_now=False,
        )
        try:
            added = await self._request("POST", "/api/v1/artist", json=payload)
            artist_id = added["id"]
            resolved_artist_name = added.get("artistName") or match["artistName"]
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 400:
                reason = describe_lidarr_error(e)
                logger.warning(f"⚠️ Lidarr: add_album failed to add '{artist_name}': {reason}")
                return {"ok": False, "error": f"Lidarr add failed: {reason}"}
            try:
                artists = await self._request("GET", "/api/v1/artist")
            except Exception as e2:
                reason = describe_lidarr_error(e2)
                return {
                    "ok": False,
                    "error": f"'{match['artistName']}' is already in Lidarr, but looking it up failed: {reason}",
                }
            existing = next(
                (a for a in artists or [] if a.get("foreignArtistId") == match["foreignArtistId"]),
                None,
            )
            if not existing:
                return {
                    "ok": False,
                    "error": f"'{match['artistName']}' is already in Lidarr, but it couldn't be found to add the album.",
                }
            artist_id = existing["id"]
            resolved_artist_name = existing.get("artistName") or match["artistName"]
        except Exception as e:
            reason = describe_lidarr_error(e)
            logger.warning(f"⚠️ Lidarr: add_album failed to add '{artist_name}': {reason}")
            return {"ok": False, "error": f"Lidarr add failed: {reason}"}

        album = await self._find_album(artist_id, album_name)
        if not album:
            return {
                "ok": True,
                "artist_name": resolved_artist_name,
                "album_title": None,
                "warning": (
                    f"Added '{resolved_artist_name}' to Lidarr, but couldn't find "
                    f"'{album_name}' yet to monitor it — check Lidarr in a moment "
                    "and monitor/search it manually if needed."
                ),
            }

        try:
            await self._request(
                "PUT", "/api/v1/album/monitor",
                json={"albumIds": [album["id"]], "monitored": True},
            )
            await self._request(
                "POST", "/api/v1/command",
                json={"name": "AlbumSearch", "albumIds": [album["id"]]},
            )
        except Exception as e:
            reason = describe_lidarr_error(e)
            logger.warning(f"⚠️ Lidarr: monitoring/searching '{album_name}' failed: {reason}")
            return {
                "ok": True,
                "artist_name": resolved_artist_name,
                "album_title": None,
                "warning": f"Added '{resolved_artist_name}' but couldn't monitor '{album.get('title')}': {reason}",
            }

        logger.info(f"📀 Lidarr: monitoring + searching '{album.get('title')}' by '{resolved_artist_name}'")
        return {"ok": True, "artist_name": resolved_artist_name, "album_title": album.get("title")}

    async def _find_album(
        self, artist_id: int, album_title: str, attempts: int = 10, delay: float = 2.0
    ) -> Optional[Dict[str, Any]]:
        """Poll for `album_title` under artist_id.

        Lidarr populates an added artist's album list asynchronously — a
        metadata-refresh job the add kicks off, not something in the add
        response itself — so the album usually isn't there yet immediately
        after POST /api/v1/artist returns.
        """
        for attempt in range(attempts):
            try:
                albums = await self._request(
                    "GET", "/api/v1/album", params={"artistId": artist_id}
                )
            except Exception as e:
                logger.warning(
                    f"⚠️ Lidarr: album list fetch failed while looking for '{album_title}': "
                    f"{describe_lidarr_error(e)}"
                )
                albums = None
            match = pick_album_match(albums or [], album_title) if albums else None
            if match:
                return match
            if attempt < attempts - 1:
                await self._sleep(delay)
        return None
