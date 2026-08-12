# AGENTS.md

MagicLists builds AI-curated smart playlists for a self-hosted
[Navidrome](https://www.navidrome.org/) server — FastAPI backend, vanilla-JS
frontend, SQLite.

## Commands

Dependencies come from `requirements.txt` + `requirements-dev.txt` — this is an
app, not an installable package, so there's no `pip install -e .`.

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m uvicorn backend.main:app --host 0.0.0.0 --port 4545 --reload
pytest -q --cov=backend --cov-report=term-missing        # fails under 80% on core
pytest tests/test_radio.py -q                            # one module
python -m unittest discover -s tests -p 'test_*.py'      # must also pass
python -m pytest e2e/ --browser chromium                 # browser end-to-end
ruff check --select=E9,F63,F7,F82 .                      # the subset CI enforces
bump-my-version bump patch
docker compose up -d --build
```

Tests are written as plain `unittest.TestCase` classes and must pass under
**both** runners — see [`docs/code-style.md`](docs/code-style.md).

The app serves on **4545**. Work in the devcontainer (`.devcontainer/`).

## Every change

- **Branch off `main`; merge via PR.** Never push to `main`. Name the branch
  `claude/<skill>/<description>`, `claude/<description>-<id>`,
  `bugfix/<description>`, or `<type>/<description>` for human work.
- **Bump the version** with `bump-my-version` — CI fails a PR that doesn't,
  including docs- and CI-only ones. Over-bumping is harmless; images deploy by
  SHA.
- **One logical change per PR**, described as Summary / Changes / Validation —
  the sections in [`.github/pull_request_template.md`](.github/pull_request_template.md).
- **Never hand-edit vendored files** — `standards/`, `.claude/skills/`, and the
  design-system CSS/JS in the frontend. They sync from upstream; changes belong
  there. See [CLAUDE.md](CLAUDE.md).
- **New env var ⇒ document it in `.env.example`.** `.env` is git-ignored.
- **This repo is public.** No credentials, and no private infrastructure detail
  in code, comments or commits.

## Non-obvious rules

Each is expanded in [`docs/architecture.md`](docs/architecture.md) — read it
before changing playlist generation.

- Guarantees about a playlist are **enforced in code after curation**, never
  merely asked for in the prompt.
- A thin library yields a **short, explained** playlist — never a padded one.
- Optional integrations (AI, Last.fm, Lidarr) **degrade**; they never fail a
  request.
- Curation prompts are **versioned files plus a registry** — never edited in
  place.

## Where the detail lives

This table is the **canonical list of what directs an agent in this repo**. The
README's documentation map is generated from it by
[`scripts/generate-docs-map.py`](scripts/generate-docs-map.py), and CI fails if
the two drift — so add or change an entry here, never there.

<!-- docs-map:canonical -->

| For | Read |
| --- | --- |
| How the app is organised and why it behaves as it does | [`docs/architecture.md`](docs/architecture.md) |
| Python style, logging, error handling | [`docs/code-style.md`](docs/code-style.md) |
| Repo standards — branching, testing, versioning, design system | [`standards/REPO-STANDARDS.md`](standards/REPO-STANDARDS.md) |
| Devcontainer, shared skills, template sync | [`CLAUDE.md`](CLAUDE.md) |
| Task procedures — opening a PR, code review, TDD — loaded on demand. **Vendored** | [`.claude/skills/`](.claude/skills/) |
| The shape every PR description takes | [`.github/pull_request_template.md`](.github/pull_request_template.md) |
| Which skills and docs are vendored, and from where | [`.github/template-sync.json`](.github/template-sync.json) |
| Every configuration variable, and what it does | [`.env.example`](.env.example) |
| Writing browser tests — the fakes, and the traps that make them pass vacuously | [`e2e/README.md`](e2e/README.md) |
