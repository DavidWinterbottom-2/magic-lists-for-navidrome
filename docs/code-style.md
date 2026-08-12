# Code style

Conventions specific to this repo. Anything not listed here follows ordinary
Python practice — this file documents what an agent would otherwise get wrong.

## Python

- **Imports** — standard library, third-party, then local, separated by blank
  lines. Local modules use relative imports: `from .radio import RadioProcessor`.
- **Types** — type hints throughout (`List`, `Optional`, `Dict` from `typing`),
  including return annotations. This codebase predates `list[str]` syntax; match
  the surrounding style rather than mixing both in one module.
- **Async** — `async def` for anything doing I/O; SQLite through `aiosqlite`,
  always with parameterised queries.
- **Formatting** — no line-length limit and **no formatter in CI**, so don't
  reformat code you aren't otherwise changing. A diff that reflows an untouched
  function is noise in review.

## Errors

- Raise `HTTPException` with a meaningful status code for API failures — `404`
  for a seed that doesn't resolve, `401` for rejected Navidrome credentials,
  `503` when Navidrome is unreachable.
- Catch narrowly. A bare `except` around a whole request hides the failures that
  most need reporting.
- Optional integrations never propagate. Log a warning, take the fallback, and —
  when the fallback materially changes the result — record it for the user (see
  [architecture.md](architecture.md)).

## Logging

- `logging.getLogger(__name__)` per module. Playlist builds and scheduler
  activity use the shared `scheduler` logger, so one stream tells the whole story
  of a build.
- Output goes to stdout **and** a rotating `scheduler.log` (5MB × 2 backups) in
  the working directory. `LOG_LEVEL` controls verbosity (`ERROR` / `INFO` /
  `DEBUG`).
- Log lines carry an emoji prefix as a scannable marker — 📻 Radio, 🔄 refresh,
  🎯 filtering, 📅 scheduling, ⚠️ degraded, ❌ failed. Match the existing set
  rather than inventing new ones.
- Log the numbers that make a build explainable: pool size in and out, how many
  tracks were dropped and why, how many were backfilled.
- **Never log secrets** — API keys, passwords, session cookies, or full request
  bodies that might carry them.

## Comments and docstrings

The comment style in this codebase is unusually explanatory, and deliberately so:
comments record *why* a decision was made and what breaks otherwise, because most
of the non-obvious code exists to work around a model's or an external API's
behaviour.

- Docstrings on public functions say what the function is for and why it exists,
  not how it works line by line.
- Comment the trade-off, the failure it prevents, or the assumption being made.
- Don't narrate what the code plainly says.

Match that density. A new guard clause with no explanation of what it's guarding
against reads as noise to the next person deciding whether it's safe to remove.

## Frontend

- Vanilla JS, no build step, no framework. Everything ships as-is from
  `frontend/static/`.
- Style through the shared design system's **tokens**. Don't hand-roll colours,
  spacing or type, and don't edit the vendored design-system files — they sync
  from upstream.
- The app is an installable PWA, but the service worker is deliberately minimal:
  network-first for navigations with an offline-page fallback, and nothing else
  precached. Don't add a precache list — it would fight the backend's own
  `Cache-Control` headers and pin a stale `app.js` in place after a deploy.

## Tests

**Where this is heading.** The agreed direction (REPO-STANDARDS §4) is
`pytest --cov=backend --cov-fail-under=80` in CI, plus a browser-driven e2e suite
for the web UI. Neither is wired up yet; CI currently runs the suite with no
coverage floor. That's a known gap, not the intended end state — so write new
tests to run under **both** runners, which plain `unittest.TestCase` classes
already do:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v   # today, in CI
python -m pytest tests/ -v                               # also passes
```

Avoid anything that would need porting later: no `unittest`-only helpers where a
plain assertion works, and no reliance on test execution order.

- One module per area under `tests/`, with a docstring saying what it covers and
  how to run it.
- **Fakes, not mocking libraries.** The suite uses hand-written fake clients
  (`FakeNav` and friends) and plain dicts for tracks, so it runs without a live
  Navidrome, Last.fm or AI provider. Keep that — it survives the pytest move
  unchanged, and it's why the tests run in under two seconds.
- The valuable tests here cover the guarantees rather than the model: that the
  seed leads, that the cap holds, that a thin pool yields a short playlist and a
  correct explanation. Test those directly — they're pure functions over track
  lists, deliberately kept separate from the I/O around them.
- That separation is the reason it works. When adding logic that must be tested,
  put it in a pure function that takes and returns data, and keep the I/O in the
  client or route around it.
