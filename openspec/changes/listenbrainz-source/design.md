## Context
Three Last.fm call sites share one tiny interface (`enabled`, `user_enabled`, `loved_track_keys`,
`similar_artists`, `top_tracks`). Callers already tolerate empty results, so the swap is mostly one new client
plus deletion.

## Assumptions (accepted at investigate; **unverified, no host reachable from the cloud session**)
- A1 Top recordings: `GET /1/stats/user/{u}/recordings?range=half_yearly`; 204 until computed; rows carry artist, title, MBID.
- A2 Loved: `GET /1/feedback/user/{u}/get-feedback?score=1&metadata=true`, paged; rows carry recording MBID and,
  with metadata, artist/title.
- A3 Similar artists: LB Labs similar-artists keyed by **artist MBID**, rows carry name and a score.
- A4 Navidrome `getArtist` returns `musicBrainzId` for the seed (OpenSubsonic extension).
- A5 Niche artists return few or no similar artists. Accepted, handled by degrading.
Task 0 verifies A1–A4 with `probe.sh`; client code is written against recorded fixtures, not guesses.

## Decisions
- D1 One `ListenBrainzClient` in `backend/listenbrainz_client.py`, same interface shape and pure parse helpers
  as the Last.fm client, so parsing is fixture-testable. Singleton accessor in `main.py` renamed.
- D2 `normalise_name`/`loved_key` move unchanged to `backend/name_matching.py` (radio/rediscover import it).
- D3 **No MusicBrainz name-search fallback in v1.** No seed MBID means no similar artists (degrade). A third
  service at 1 req/s is the heaviest unknown; add only if the probe shows Navidrome rarely has MBIDs.
- D4 Navidrome client gets one small method returning the artist's MBID (from `getArtist`), nothing else.
- D5 Do not cache empty results produced by errors or 204 (Last.fm cached them for 6 h, which would hide the
  first scrobbles). Successful loved lookups keep the 6 h cache.
- D6 Re-Discover mode string changes; only `rediscover.py` and its test reference the old one.

## Risks
Probe contradicts A3/A4 → drop similar artists to a follow-up PR (Radio stays model-only); loved/top tracks
and the Last.fm removal still ship before 12-01.

## Agreed at spec (2026-11-22, David)
1. The orchestrator's Pi bridge runs `probe.sh` and pastes redacted output on #65 (task 0.1). If it hasn't by 2026-11-25, group 3 (similar artists) moves to a follow-up.
2. No MusicBrainz name-search fallback in v1 (D3).
3. One PR; group 3 droppable at the 11-25 decision point.
4. D5 (no caching of error/204 empties), D6 (`LISTENING_HISTORY_FALLBACK`, "Listening History Favourites") and version 0.7.0 with no new `CHANGELOG.md` (PR description carries the notes) accepted as written.
