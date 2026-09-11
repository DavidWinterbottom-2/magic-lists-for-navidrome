# Review log

Dated code- and architecture-review records for this repo (REPO-STANDARDS §9),
newest first. One line per review:

```
- YYYY-MM-DD | <type> | <scope> | <note or PR link>
```

where `<type>` is `code-review` or `architecture-review`.

- 2026-09-11 | code-review | backend/schemas.py playlist_length bounds (1/N, re-verifying 2026-08-17 findings) | Verified all 9 items from the 2026-08-17 reviews before starting: the Subsonic token+salt log leak is RESOLVED (`navidrome_client.py`'s `_redact_auth()` now strips `t`/`s` at both the delete and retry paths); the recipe `eval()` finding is now WORSE than recorded — it was logged as "currently unreachable" but is confirmed live in the main curation path (`recipe_manager.py:105`, `apply_recipe` called from every `ai_client.py` curation entry point, and 3 bundled recipe JSON files do carry `{{MATH:...}}` expressions); the other 7 (pervasive bare excepts in database.py/rediscover.py, dead payload-building in ai_client.py, unbounded playlist_length, no ruff config, coverage gate scoped to an 8-module include-list ~60% backend-wide, process-lifetime httpx singletons with no cleanup, non-expiring cached Subsonic token) are all still open. Applied this round: `playlist_length` was a plain unconstrained `int` in all 5 request schemas — the UI only ever offers 25/50/100 (radio buttons, not free text) but the API had no server-side bound, so a direct request could ask for an arbitrarily large playlist (each track costs a Navidrome + AI-provider round trip during curation). Added `Field(gt=0, le=MAX_PLAYLIST_LENGTH)` (1000 — well above the UI's max, so no legitimate use is affected) across all 5 declarations. New regression test `test_playlist_length_is_bounded` (0/-5/1001 → 422, 1000 → 200). Full suite (311 tests) green, `ruff check --select=E9,F63,F7,F82` clean (magic-lists-for-navidrome 0.3.9)

- 2026-08-17 | code-review | backend/ (auth, navidrome_client, lastfm_client, ai_client, ai_response, recipe_manager, database, schemas) | Subsonic token+salt leak into logs on navidrome_client delete/retry paths (:1291/:194); eval() on recipe MATH templates (recipe_manager:105, currently unreachable); pervasive bare excepts; dead payload-building in ai_client; no bounds on playlist_length. Tests + version-bump green; no ruff config (§10); coverage gate scoped to an 8-module include-list (~60% backend-wide).
- 2026-08-17 | architecture-review | Navidrome/Last.fm clients, AI provider abstraction, recipe/index-based curation, SQLite persistence, Entra OIDC gate | Sound layering — index-based AI track mapping is injection-safe, parameterized SQL, degrade-to-empty Last.fm client; weak spots are process-lifetime singletons with no httpx cleanup and a non-expiring cached Subsonic session token.
