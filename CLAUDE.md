# magic-lists-for-navidrome — conventions for Claude Code

A Python web app that builds "magic" (smart) playlists for
[Navidrome](https://www.navidrome.org/). Dev happens in the VS Code
devcontainer (`.devcontainer/`); the app serves on port **4545**.

## Standards

This repo follows the winterbottom.xyz standards. The **canonical** copies live
in the [`devcontainer-sandbox`](https://github.com/DavidWinterbottom-2/devcontainer-sandbox)
template under `standards/`; vendored copies are kept here for convenience and
are refreshed automatically (see "Template sync" below) — treat upstream as the
source of truth and never hand-edit the vendored copies:

- [`standards/REPO-STANDARDS.md`](standards/REPO-STANDARDS.md) — repo layout,
  branching, tests/coverage, PRs, reviews (REPO-STANDARDS §1–§9).

`HOSTING-SECURITY.md` — the authentication contract for anything hosted publicly
— is **deliberately not vendored here**, because this repo is public and that doc
describes private infrastructure. Read it from the template when this app is
wired into docker-infra as a public service; don't copy it in.

## CI and versioning

The version lives in `pyproject.toml`; bump it with
`bump-my-version bump patch|minor|major`, never by hand. `version-check.yml`
(check `version-bumped`) fails a PR that doesn't bump it, **unless the PR is
docs-only**.

Docs-only is decided by one script,
[`.github/scripts/code-changed.sh`](.github/scripts/code-changed.sh), which both
`version-check.yml` and `ci.yml` call. It allowlists docs: `*.md`, `docs/`,
`openspec/`, `.claude/` and `LICENSE`. Everything else is code, including
`.github/**`, and anything under `backend/`, `frontend/` or `recipes/` (the
folders the Dockerfile copies into the image), even a `.md` there. If you add a
doc that the app or a test reads, or a Dockerfile `COPY` that reaches a docs
path, update the script.

In `ci.yml`, `lint` always runs (its docs-map check is what reads the docs);
`test` and `e2e` are skipped on docs-only and draft PRs by a job-level `if`, so
the skipped checks report as passing. Marking a draft ready runs them. A new push
cancels a PR's still-running CI. Pushes to `main` always run the full suite and
`publish.yml`, and are never cancelled by CI's concurrency group.

## Dev environment

Templated off `devcontainer-sandbox` (REPO-STANDARDS §7). The container adopts
the template's lean [`post-create.sh`](.devcontainer/post-create.sh), which
installs `requirements.txt` **and** clones + installs your personal dotfiles, so
your shell/editor config comes with you. Your host SSH keys are bind-mounted in
(`~/.ssh` → `/home/vscode/.ssh`) and the container runs as the non-root `vscode`
user. Keep the container aligned with the template as it evolves (the
`devcontainer-from-sandbox` skill covers this).

## Shared skills

The shared Claude Code skills library lives in the template's `.claude/skills/`.
The template's `.claude/skills/.hermes-skills` manifest decides which skills are
shared; the template-sync workflow copies those into [`.claude/skills/`](.claude/skills/)
and never deletes a skill that exists only here, so a repo-local skill is safe.

## Template sync

[`.github/workflows/template-sync.yml`](.github/workflows/template-sync.yml) is a
verbatim copy of the template's workflow. It runs weekly and opens up to two PRs
when this repo has drifted from the template:

- **`template-sync/skills-content`**: `.claude/skills/**` only. It requests
  auto-merge (squash) as soon as it opens.
- **`template-sync/skills`**: the files that run with real permissions:
  `.claude/agents/`, `.claude/hooks/` (including the claude-tracker
  `report.sh`/`report.py` hooks), the hook registrations in
  `.claude/settings.json` (merged in, so the repo's own hooks stay), this
  workflow, `claude.yml`, `labels.json` + `labels.yml`, and the PR template.
  Always reviewed by hand.

It needs a `TEMPLATE_SYNC_TOKEN` repo secret: a PAT with Contents (read/write),
Workflows (write) and Pull requests (write) on this repo, plus Contents:read on
the template. A classic PAT needs the `repo` + `workflow` scopes. The PAT is
required because the default `GITHUB_TOKEN` can't push `.github/workflows/*`
files and can't open PRs here.

The workflow does **not** sync `standards/`. To refresh
[`standards/REPO-STANDARDS.md`](standards/REPO-STANDARDS.md), run
[`scripts/sync-from-template.sh`](scripts/sync-from-template.sh) by hand. It
reads [`.github/template-sync.json`](.github/template-sync.json) and still
leaves out `HOSTING-SECURITY.md`.

To opt this repo out of claude-tracker reporting, commit an empty
`.claude/tracker-off`.
