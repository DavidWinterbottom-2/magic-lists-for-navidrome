## 0. Probe (first; blocks the rest)
- [x] 0.1 (probe output posted on #65 2026-11-22: A1 204 only, A2 and A3 confirmed, A4 not run and still open) Run `probe.sh` from a host that reaches ListenBrainz/MusicBrainz/Navidrome (orchestrator Pi bridge or David). Paste redacted output on #65; save trimmed JSON as test fixtures. Verify: A1–A4 each marked confirmed/refuted on the issue.
- [ ] 0.2 **Decision point 2026-11-25:** if A3 or A4 is refuted or the probe is not run, drop group 3 to a follow-up issue and ship the rest.

## 1. Client and matching (tests first)
- [x] 1.1 `name_matching.py` (moved helpers); update imports. Verify: existing folding tests pass unchanged.
- [x] 1.2 `ListenBrainzClient`: parse helpers + `loved_track_keys`, `top_tracks`; 204/error/timeout → empty, errors not cached. Verify: fixture tests incl. 204, malformed, empty.

## 2. Loved boost and Re-Discover
- [x] 2.1 `apply_loved_signal` uses the new client; MBID-first then name matching. Verify: tests for MBID match, name fallback, unconfigured.
- [x] 2.2 Re-Discover top-tracks fallback, neutral label/mode. Verify: ported `test_rediscover_lastfm` cases pass; falls through below min.

## 3. Similar artists (droppable per 0.2)
- [x] 3.1 Navidrome artist-MBID method; `similar_artists(mbid)`. Verify: fixture + fake-Navidrome tests, no-MBID degrade test.
- [x] 3.2 Radio uses it; `ai_client` prompt wording provider-neutral. Verify: `test_radio*` ported; ranking and out-of-library filter asserted.

## 4. Removal, health, docs, release
- [x] 4.1 Delete `lastfm_client.py`, `LASTFM_*`, Last.fm tests; ListenBrainz health check (never fails startup). Verify: `grep -ri lastfm` empty outside `docs/reviews/LOG.md`; health tests for all four states.
- [x] 4.2 `.env.example`, README, SETUP, docs/architecture.md, AGENTS.md. Verify: grep above.
- [x] 4.3 Bump 0.7.0 in `pyproject.toml`; full test suite + coverage gate + CI green. Verify: `version-bumped` passes.
- [ ] 4.4 Open docker-infra follow-up issue (`LISTENBRAINZ_USERNAME`, optional `LISTENBRAINZ_URL` in `magiclists/docker-compose.yml`) at stage:define, linking #65.

## 5. Deploy (after merge)
- [ ] 5.1 Pi `.env` + restart via orchestrator bridge; host `/health` shows new version. 5.2 David enables Navidrome ListenBrainz scrobbling. 5.3 Archive change; record cost vs estimate.

## Cost estimate (remaining, list price, indicative)
Implement (groups 1–4): roughly 1.5–2.5M input tokens with caching across ~60–90 calls, ~$8–15 on this model class; test + review agents add ~$5–10; deploy/archive ~$2–3. Total about **$15–28**. A fresh session for implement avoids re-reading this long thread (cache expiry cost), so recommend a fresh session primed with `tasks.md` and the issue. Estimate is rough: no pricing lookup was possible offline.
