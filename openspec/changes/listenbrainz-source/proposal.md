## Why

Last.fm API keys need Last.fm Pro from **2026-12-01**; docker-infra drops `LASTFM_API_KEY`/`LASTFM_USERNAME`
(docker-infra#462, PR #492 on hold until this ships). Three features lose their data source: Radio's out-of-library
album suggestions, the loved-track boost (every playlist type), and Re-Discover's top-tracks fallback.
Issue: #65.

## What Changes

- **Add** `ListenBrainzClient` (username-only, public reads, no token) providing similar artists, loved tracks
  and 6-month top tracks, degrading to empty on any failure.
- **Rewire** the three call sites (`radio.py`, `main.apply_loved_signal`, `rediscover.py`) to it.
- **Remove** Last.fm entirely: `lastfm_client.py`, `LASTFM_*` env, health check, docs, tests
  (agreed at define, decision 2). The name-folding helper moves to `backend/name_matching.py` first.
- **Rename** Re-Discover's fallback label/mode to provider-neutral (`LISTENING_HISTORY_FALLBACK`,
  "Listening History Favourites").
- **Bump** version 0.6.10 → 0.7.0 (a documented env var is removed).
- **Out of this repo:** docker-infra follow-up issue for `LISTENBRAINZ_*` compose passthrough; Pi `.env`
  and restart via the orchestrator's Pi bridge after merge.

## Non-goals (keeps it deliverable by 2026-12-01)

No token/private data, no feedback writes, no MusicBrainz name search (see design D3), no caching beyond what
exists today, no UI change, no merging of providers.

## Capabilities

### New Capabilities
- `listening-history`: ListenBrainz-backed similar artists, loved tracks and top tracks, and how Radio,
  scoring and Re-Discover consume them.

## Impact

`backend/` (new client, `name_matching.py`, radio, rediscover, main, health check, `navidrome_client.py` artist
MBID and track `mbid`, `track_scoring.py`/`lidarr_client.py` wording, ai_client wording), AGENTS.md, docs/code-style.md, `.env.example`, README,
SETUP, docs/architecture.md, tests (replace ~80 Last.fm references with ListenBrainz fixtures),
`pyproject.toml` version and coverage list, CI (`e2e` pinned to `ubuntu-24.04`), `scripts/generate-docs-map.py`
(skips `openspec/changes/`). Cross-repo: docker-infra compose ([docker-infra#509](https://github.com/DavidWinterbottom-2/docker-infra/issues/509)).

## Deadline risk

9 days from 2026-11-22. **At risk** only through the unverified assumptions (design "Assumptions"). Fixed
decision points are in `tasks.md`. Value on 12-01 is partial regardless: loved/top tracks stay thin until
Navidrome scrobbling to ListenBrainz (not yet on) has run for a while.
