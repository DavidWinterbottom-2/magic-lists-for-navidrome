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

import logging
import os
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
    search_now: bool = True
) -> Dict[str, Any]:
    """Shape one `artist/lookup` result into an `artist` POST body.

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
        "addOptions": {"searchForMissingAlbums": search_now},
    }


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


class LidarrClient:
    """Thin async wrapper over Lidarr's REST API for adding artists."""

    def __init__(self):
        self.base_url = (os.getenv("LIDARR_URL") or "").strip().rstrip("/")
        self.api_key = (os.getenv("LIDARR_API_KEY") or "").strip()
        self.quality_profile_id = (os.getenv("LIDARR_QUALITY_PROFILE_ID") or "").strip()
        self.root_folder_path = (os.getenv("LIDARR_ROOT_FOLDER_PATH") or "").strip()
        self.metadata_profile_id = (os.getenv("LIDARR_METADATA_PROFILE_ID") or "1").strip()
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(20.0))

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

    async def add_artist(self, artist_name: str) -> Dict[str, Any]:
        """Look up `artist_name` in Lidarr and add it for monitoring/download.

        Returns {"ok": True, "artist_name": ...} on success, or
        {"ok": False, "error": ...} when the client isn't configured, no match
        was found, or the add itself failed (including "already added").
        """
        if not self.enabled:
            return {"ok": False, "error": "Lidarr isn't fully configured for adding artists."}

        try:
            results = await self._request(
                "GET", "/api/v1/artist/lookup", params={"term": artist_name}
            )
        except Exception as e:
            reason = describe_exception(e)
            logger.warning(f"⚠️ Lidarr: artist lookup failed for '{artist_name}': {reason}")
            return {"ok": False, "error": f"Lidarr lookup failed: {reason}"}

        match = pick_lookup_match(results or [], artist_name)
        if not match:
            return {"ok": False, "error": f"No Lidarr match found for '{artist_name}'."}

        payload = build_add_artist_payload(
            match,
            quality_profile_id=int(self.quality_profile_id),
            root_folder_path=self.root_folder_path,
            metadata_profile_id=int(self.metadata_profile_id),
        )
        try:
            added = await self._request("POST", "/api/v1/artist", json=payload)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                return {"ok": False, "error": f"'{match['artistName']}' is already in Lidarr."}
            reason = describe_exception(e)
            logger.warning(f"⚠️ Lidarr: add_artist failed for '{artist_name}': {reason}")
            return {"ok": False, "error": f"Lidarr add failed: {reason}"}
        except Exception as e:
            reason = describe_exception(e)
            logger.warning(f"⚠️ Lidarr: add_artist failed for '{artist_name}': {reason}")
            return {"ok": False, "error": f"Lidarr add failed: {reason}"}

        logger.info(f"📀 Lidarr: added '{added.get('artistName')}' (ID {added.get('id')})")
        return {"ok": True, "artist_name": added.get("artistName") or match["artistName"]}
