# Review log

Dated code- and architecture-review records for this repo (REPO-STANDARDS §9),
newest first. One line per review:

```
- YYYY-MM-DD | <type> | <scope> | <note or PR link>
```

where `<type>` is `code-review` or `architecture-review`.

- 2026-08-17 | code-review | backend/ (auth, navidrome_client, lastfm_client, ai_client, ai_response, recipe_manager, database, schemas) | Subsonic token+salt leak into logs on navidrome_client delete/retry paths (:1291/:194); eval() on recipe MATH templates (recipe_manager:105, currently unreachable); pervasive bare excepts; dead payload-building in ai_client; no bounds on playlist_length. Tests + version-bump green; no ruff config (§10); coverage gate scoped to an 8-module include-list (~60% backend-wide).
- 2026-08-17 | architecture-review | Navidrome/Last.fm clients, AI provider abstraction, recipe/index-based curation, SQLite persistence, Entra OIDC gate | Sound layering — index-based AI track mapping is injection-safe, parameterized SQL, degrade-to-empty Last.fm client; weak spots are process-lifetime singletons with no httpx cleanup and a non-expiring cached Subsonic session token.
