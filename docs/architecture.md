# Architecture

How MagicLists is put together, and the rules behind how it behaves. Read this
before changing playlist generation.

## Shape of the app

A FastAPI backend serves both a JSON API and a small vanilla-JS single-page
frontend (installable as a PWA). Playlists are written to Navidrome over the
Subsonic API and *also* recorded in a local SQLite database — Navidrome holds the
playable playlist, the local database holds everything Navidrome can't: the
seed, the schedule, the curator's reasoning, album suggestions, and a record of
how the playlist was last built.

The backend modules split by responsibility:

| Concern | Where |
| --- | --- |
| Routes, scheduler, logging setup | `backend/main.py` |
| Candidate pooling and post-curation guarantees | `backend/radio.py`, `backend/rediscover.py` |
| Engagement scoring, payload trimming | `backend/track_scoring.py` |
| Provider-agnostic LLM calls | `backend/ai_client.py`, `backend/services/ai_providers.py` |
| External APIs | `backend/navidrome_client.py`, `backend/lastfm_client.py` |
| Login gate (Entra ID OIDC) | `backend/auth.py` |
| Persistence | `backend/database.py` |
| Startup diagnostics | `backend/services/health_check_service.py` |

Curation prompts live in `recipes/`, the frontend in `frontend/`, tests in
`tests/`.

## Generation pipeline

Every playlist type follows the same shape:

```
resolve the request  →  gather a candidate pool from the library
                     →  score and trim the pool to an affordable payload
                     →  LLM curates against a recipe
                     →  enforce the guarantees in code
                     →  write to Navidrome + record locally
```

The interesting decisions are in the last two steps.

## The four rules

### 1. Guarantees are enforced in code, not in the prompt

A recipe *asks* the model for style coherence, artist diversity and a cap on any
one artist. A model cannot be relied on for a hard guarantee, so anything that
must hold is applied afterwards, in Python, over whatever the model returned.

Radio is the worked example: `cap_seed_artist` holds the seed to its share of the
station, `promote_seed_first` guarantees the station opens with its seed, and
`enforce_artist_cap` applies the per-artist limit. Follow that pattern when
adding a constraint — express it in the recipe *and* enforce it after.

### 2. Short and explained beats padded

When the candidate pool can't fill the requested length without breaking the
rules, the playlist comes back **short**. It is never padded with more tracks by
an already-used artist, because a padded playlist silently misrepresents what the
library can do.

The gap is then explained to the listener — how many tracks were delivered
against how many were asked for, how many candidates matched, and what to add to
fix it. A *full-length* playlist built from a degraded pool is reported too: if a
lookup failed and the build fell back, the result will resemble the last one, and
that needs saying rather than leaving the user to wonder.

### 3. Optional integrations degrade, never fail

AI curation, Last.fm and Lidarr are all optional, and each has a defined fallback:

| Integration | Absent or failing ⇒ |
| --- | --- |
| AI provider | play-count and recency ordering |
| Last.fm | Navidrome-only signals; the model suggests albums from its own knowledge |
| Lidarr | album suggestions render as plain text instead of links |

A failing optional dependency logs a warning and takes the fallback. It never
raises out of a request. When the fallback materially changes the result, record
that for the user rather than swallowing it silently.

### 4. Recipes are versioned, never edited in place

A curation prompt change lands as a **new file** with the registry pointed at it —
not as an edit to the existing one. That way a regression traces to a specific
recipe and rolls back by flipping a single registry entry. Superseded recipes
move to the archive directory rather than being deleted.

Recipes support `{{PLACEHOLDER}}` substitution and `{{MATH:...}}` expressions
evaluated against the requested track count, so proportional rules (a 20% cap, a
40% first tranche) scale with playlist length.

## Scheduling and rebuilds

A scheduled playlist stores its seed in a form the refresh path can re-resolve —
Radio encodes `radio:{seed_type}:{seed_id}` — so a refresh regenerates the
playlist from the same seed against the *current* library.

The manual **Recreate** button reuses the scheduler's own refresh functions
rather than reimplementing them, so a manual rebuild and an automatic one produce
identical results. The one deliberate difference: Recreate propagates errors so
the UI can report what went wrong, where the scheduler logs and moves on.

The scheduler wakes twice a day (01:01 and 13:01) and rebuilds anything due,
with a 7-day grace window so refreshes missed while the app was offline still
happen.

## Deployment shape

Pushes to `main` publish a multi-arch image (amd64 + arm64 — the target is a
Raspberry Pi) to GHCR, tagged `:latest` and with the commit SHA. Watchtower
follows `:latest`; the SHA tag is how a build is pinned or rolled back. The
running service is therefore identified by its image SHA, not by the version in
`pyproject.toml` — that version exists to satisfy the per-PR bump gate.

The app holds Navidrome credentials and the AI key server-side, so a
publicly-reachable deployment must run with the Entra login gate on. With auth
enabled and any credential missing, the app refuses to start rather than coming
up unprotected — that check is deliberate, and shouldn't be softened into a
warning.
