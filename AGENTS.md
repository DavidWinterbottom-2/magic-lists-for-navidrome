# Agent Development Guide

Conventions for anyone — human or agent — working in this repo. For what the app
*is* and how to deploy it, see [README.md](README.md); for hands-on configuration
and verification, [SETUP.md](SETUP.md).

This is the winterbottom.xyz fork of
[rsynnot/magic-lists-for-navidrome](https://github.com/rsynnot/magic-lists-for-navidrome)
and is held to [`standards/REPO-STANDARDS.md`](standards/REPO-STANDARDS.md) —
section references below (§1, §2, …) point there.

## Layout

```
backend/            FastAPI app
  main.py             routes, scheduler, logging setup
  radio.py            Radio candidate pool + post-curation guarantees
  rediscover.py       Re-Discover logic
  track_scoring.py    engagement scoring / payload trimming
  ai_client.py        provider-agnostic curation calls
  navidrome_client.py Subsonic/Navidrome API client
  lastfm_client.py    optional Last.fm client
  auth.py             Entra ID (Azure AD) OIDC gate
  database.py         SQLite (aiosqlite) persistence
  schemas.py          Pydantic request/response models
  errors.py           error-description helpers
  services/           health checks, AI provider registry
frontend/           templates/ + static/ (vanilla JS SPA, PWA shell)
recipes/            versioned curation prompts + registry.json
tests/              stdlib unittest suite
standards/          vendored REPO-STANDARDS.md — never hand-edit
scripts/            template sync, icon generation
```

## Dev environment

Work in the VS Code devcontainer (`.devcontainer/`, "Reopen in Container" or
Codespaces), templated off
[`devcontainer-sandbox`](https://github.com/DavidWinterbottom-2/devcontainer-sandbox)
(§7). The app serves on **port 4545**.

```bash
# Run with reload
python -m uvicorn backend.main:app --host 0.0.0.0 --port 4545 --reload

# Tests (stdlib unittest — there is no pytest config in this repo)
python -m unittest discover -s tests -p 'test_*.py' -v
python -m unittest tests.test_radio -v          # a single module

# Lint — the hard-error subset CI enforces (no ruff config yet, so a full
# `ruff check` drowns the signal in legacy noise)
ruff check --select=E9,F63,F7,F82 .
python -m compileall backend tests
```

Docker:

```bash
docker compose up -d --build     # build from source
docker compose pull && docker compose up -d   # run the published GHCR image
docker compose logs -f magiclists
```

## Branch and PR workflow

- `main` is protected; everything lands via PR (§1). Never push to `main`.
- Branch names encode who and why (§1):
  - `claude/<skill-name>/<description>` — Claude following a skill
  - `claude/<description>-<id>` — Claude, ad-hoc
  - `bugfix/<description>` — bug fixes
  - `<type>/<description>` (`feat`, `chore`, `docs`, …) — human work
- **One logical change per PR.** Keep diffs reviewable.
- PR descriptions use **Summary / Changes / Validation** (§3) — what changed and
  why, the files touched, and how it was verified (tests run, `docker compose
  config`, a `/health` check). The shared `.github/pull_request_template.md`
  isn't vendored here yet, so write the three sections by hand.
- Bump the version in the same PR (below) — CI fails without it.

## Versioning

[`pyproject.toml`](pyproject.toml) holds the single source of truth for the
version (§2). Bump it with the tool, never by hand (it's a dev-only tool, not in
`requirements.txt` — `pip install bump-my-version`):

```bash
bump-my-version bump patch      # or minor / major
```

The check fires on **every** PR, including docs- and CI-only ones. Over-bumping
is harmless — images deploy by SHA — so bump rather than reaching for a path
filter.

[`version-check.yml`](.github/workflows/version-check.yml) fails any PR that
doesn't bump it. The published image is separate: `:latest` plus a commit-SHA
tag, so the *running* service is identified by its image SHA, not this version.

## CI

[`ci.yml`](.github/workflows/ci.yml) runs on every PR and push to `main`:
the hard-error ruff subset, `compileall`, and the unittest suite.

Known gaps against §4, worth closing rather than working around: there's no
coverage gate (§4 wants ≥80% enforced in CI) and no browser-driven e2e suite
despite this being a web UI. New behaviour still needs unit tests — mock the
network in them; `tests/` has the existing patterns.

## Recipes

Curation prompts live in [`recipes/`](recipes/) as **versioned JSON files**
selected through [`registry.json`](recipes/registry.json). A prompt change lands
as a **new file** (`radio_v1_002.json`) with the registry pointed at it — never
as an edit in place, so a regression can be traced to a specific recipe and
rolled back by flipping one entry. Superseded recipes move to `recipes/archive/`.

Recipes support `{{PLACEHOLDER}}` substitution and `{{MATH:...}}` expressions
evaluated against the requested track count.

## Model-output guarantees

Anything that *must* hold about a playlist is enforced in code after curation,
not merely asked for in the prompt — a model can't be relied on for a hard
guarantee. See `backend/radio.py`: `cap_seed_artist`, `promote_seed_first`,
`enforce_artist_cap`. Follow that pattern when adding constraints, and prefer
returning a **shorter, honest** result over a padded one — `build_shortfall`
exists to explain the gap to the listener.

## Code style

### Imports
- Standard library, then third-party, then local — blank line between groups
- Relative imports for local modules: `from .module import Class`

### Formatting
- 4-space indentation; reasonable line length (no strict limit enforced)
- Blank lines between functions and classes

### Types
- Type hints throughout (`List`, `Optional`, `Dict`, `Union` from `typing`)
- Return annotations on all functions

### Naming
- `snake_case` variables/functions/modules, `PascalCase` classes,
  `UPPER_CASE` constants, `_leading_underscore` private methods

### Error handling
- `try`/`except` for expected failures; specific exception types where possible
- Raise `HTTPException` with an appropriate status code for API errors
- Optional integrations (Last.fm, Lidarr, AI) must **degrade**, never fail the
  request — log a warning and fall back
- When a fallback materially changes the result, record it for the user
  (`pool_warnings` → `build_shortfall`) rather than failing silently

### Async
- `async def` for I/O; `await` external calls
- SQLite via `aiosqlite`; parameterised queries only

### Logging
- `logging.getLogger(__name__)` per module; `scheduler_logger` for
  playlist-build and scheduler activity
- Logs go to stdout **and** rotating `scheduler.log` (5MB × 2 backups) in the
  working directory; `LOG_LEVEL` controls verbosity
- Emojis are used as scannable prefixes in scheduler logs (📻 Radio, 🔄 refresh,
  ❌ error) — match the surrounding style
- **Never log secrets** — API keys, passwords, session cookies

### Documentation
- Docstrings on public functions: what it does and *why*, not how
- The comments in this codebase explain non-obvious decisions and trade-offs.
  Match that density — don't narrate what the code plainly says

## Security and secrets

- `.env` is git-ignored; `.env.example` is committed and must document every new
  variable (§5)
- Never commit credentials, tokens or session secrets
- This repo is **public**. `HOSTING-SECURITY.md` describes private
  infrastructure and is deliberately **not vendored** here — read it from the
  `devcontainer-sandbox` template instead, and keep private topology out of
  commits, comments and docs
- The app holds Navidrome credentials and the AI key server-side, so a
  publicly-reachable deployment must run with the Entra gate on
  (`AUTH_DISABLED=false`); with auth on and credentials missing, the app refuses
  to start rather than come up unprotected

## Vendored files — don't hand-edit

Refreshed automatically by
[`template-sync.yml`](.github/workflows/template-sync.yml), which opens a PR on
drift:

- `standards/REPO-STANDARDS.md`
- `.claude/skills/` (the shared subset declared in
  [`.github/template-sync.json`](.github/template-sync.json))
- `frontend/static/winterbottom.css` and `winterbottom-theme.js` — the shared
  design system (§8). Style through its **tokens**; don't hand-roll colours,
  spacing or type

Changes to any of these belong upstream in `devcontainer-sandbox` or
`docker-infra/design-system`.

## Deployment

Pushing to `main` runs [`publish.yml`](.github/workflows/publish.yml), which
cross-builds `linux/amd64` + `linux/arm64` (the target is a Raspberry Pi) and
pushes `ghcr.io/davidwinterbottom-2/magic-lists-for-navidrome` tagged `:latest`
and `sha-<commit>`. Watchtower follows `:latest`; `MAGICLISTS_VERSION=sha-<commit>`
pins a build for rollback.

```bash
docker compose up -d                          # uses the published image
docker run -d --name magiclists -p 4545:4545 \
  -e NAVIDROME_URL=... -e NAVIDROME_USERNAME=... -e NAVIDROME_PASSWORD=... \
  -e DATABASE_PATH=/app/data/magiclists.db \
  -v ./magiclists-data:/app/data \
  ghcr.io/davidwinterbottom-2/magic-lists-for-navidrome:latest
```

The container listens on **4545** and needs `/app/data` on a persistent volume —
that's the SQLite database holding playlists and schedules.
