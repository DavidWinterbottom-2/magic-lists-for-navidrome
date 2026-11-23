## ADDED Requirements

### Requirement: ListenBrainz is configured by username only
The app SHALL read ListenBrainz public data using `LISTENBRAINZ_USERNAME` and an optional
`LISTENBRAINZ_URL` (default `https://api.listenbrainz.org`). No token SHALL be required or read.

#### Scenario: Unconfigured
- **WHEN** `LISTENBRAINZ_USERNAME` is empty
- **THEN** loved and top tracks are empty, and Radio/Re-Discover/scoring behave as Navidrome-only

### Requirement: Failures never break a playlist build
Any ListenBrainz error, timeout, non-JSON body or HTTP 204 (no stats yet) SHALL yield an empty result
and a logged message (a warning; 204 is an expected state and logged at info), never an exception to the caller. A failure after the first page of a paged lookup keeps the rows already read.

#### Scenario: Stats not ready
- **WHEN** top-tracks returns 204
- **THEN** Re-Discover falls through to its starred/most-played fallbacks unchanged

### Requirement: Loved tracks boost every playlist type
Tracks loved on ListenBrainz SHALL be flagged `loved` (the existing +50 score) in addition to Navidrome stars.
A track SHALL be loved if its recording MBID matches a loved recording's MBID OR its folded (artist, title) key matches a loved track's key (lenient: either is enough, and one does not veto the other).

#### Scenario: MBID match
- **WHEN** a library track carries a recording MBID equal to a loved recording's MBID
- **THEN** it is marked loved even if the titles differ

#### Scenario: Conflicting MBIDs, equal names
- **WHEN** a library track and a loved recording carry different recording MBIDs but equal folded artist and title
- **THEN** it is marked loved (agreed at test: lenient matching)

#### Scenario: Name fallback
- **WHEN** either side lacks an MBID but folded artist and title are equal
- **THEN** it is marked loved

### Requirement: Radio grounds album suggestions in similar artists
Radio SHALL resolve the seed artist's MBID from Navidrome, fetch ListenBrainz similar artists, drop those
whose folded name is already in the library, and pass the top 8 (ranked) as preferred album artists.

#### Scenario: Seed has no MBID
- **WHEN** Navidrome returns no MBID for the seed artist
- **THEN** no similar artists are used and the curator suggests from its own knowledge, as before

### Requirement: Re-Discover uses ListenBrainz top tracks first
Re-Discover's first fallback SHALL resolve ListenBrainz top recordings (about 6 months, up to 200) to library
tracks (fetched in pages of up to 100, the stats endpoint's cap), labelled with theme "Listening History Favourites" and mode `LISTENING_HISTORY_FALLBACK`. Fewer
resolved tracks than the existing minimum SHALL fall through to the next fallback.

#### Scenario: Enough resolved
- **WHEN** at least the minimum number of top recordings resolve to library tracks
- **THEN** the playlist uses mode `LISTENING_HISTORY_FALLBACK`

#### Scenario: Too few resolved
- **WHEN** fewer resolve
- **THEN** the next fallback is used

### Requirement: Health check reports ListenBrainz
The health check SHALL report ListenBrainz status (unconfigured / user not found / reachable / unreachable)
and SHALL NOT fail startup in any case.

#### Scenario: Host unreachable
- **WHEN** ListenBrainz cannot be reached
- **THEN** the check reports a warning and the app starts normally

### Requirement: Last.fm is removed
No Last.fm client, env var, health check, doc mention or test SHALL remain, except historical records
(`docs/reviews/LOG.md` and the OpenSpec change history under `openspec/`).

#### Scenario: Repository scan
- **WHEN** the repo is searched case-insensitively for `lastfm` or `last.fm`
- **THEN** only `docs/reviews/LOG.md` and files under `openspec/` match
