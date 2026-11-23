"""
Provider-neutral name matching.

Folds artist/title strings to comparable keys so tracks from an external
listening-history source (ListenBrainz) can be matched against
the local library despite tagging differences. Pure functions, no I/O.
"""

import re
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple

_LEADING_ARTICLE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalise(value: Optional[str]) -> str:
    """Fold a title/artist to a match key: lowercase, no articles, alnum only.

    An external source's spelling of an artist rarely matches a library's tag character for
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

    Exposed so callers can test an external artist name against the local library
    with the same case/article/accent folding used for loved-track matching.
    """
    return _normalise(name)


def mark_loved(
    tracks: List[Dict[str, Any]],
    loved_keys: Set[Tuple[str, str]],
    loved_mbids: Optional[Set[str]] = None,
) -> int:
    """Flag loved tracks in place. Returns the number newly marked.

    Sets `track['loved'] = True`, the exact signal `score_tracks_by_user_engagement`
    already rewards (+50). A track matches when its recording MBID is in
    `loved_mbids` OR its folded (artist, title) key is in `loved_keys`; differing
    MBIDs do not veto an equal name (lenient matching, agreed at test for #65).
    """
    if not loved_keys and not loved_mbids:
        return 0
    marked = 0
    for track in tracks:
        if track.get("loved"):
            continue  # already loved (e.g. via a Navidrome star) — don't recount
        mbid = (track.get("mbid") or "").strip().lower()
        if (loved_mbids and mbid and mbid in loved_mbids) or \
                loved_key(track.get("artist"), track.get("title")) in loved_keys:
            track["loved"] = True
            marked += 1
    return marked
