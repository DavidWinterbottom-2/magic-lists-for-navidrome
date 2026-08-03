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
— is **deliberately not vendored here**. This repo is a public fork, and that doc
describes the private estate (the shared Entra ID env-var contract, the MCP
`x-api-key` gate, the Cloudflare vhost topology, and the names of other
first-party services). Read it from the template when this app is wired into
docker-infra as a public service; don't copy it in.

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
This repo vendors the subset relevant to app development under
[`.claude/skills/`](.claude/skills/). The synced set is declared in
[`.github/template-sync.json`](.github/template-sync.json) (`shared_skills`); to
carry another shared skill, add its directory name there. The repo's own
operational skills, if any, go in `own_skills` and are never touched by the sync.

## Template sync

[`.github/workflows/template-sync.yml`](.github/workflows/template-sync.yml) runs
[`scripts/sync-from-template.sh`](scripts/sync-from-template.sh) on a schedule and
opens a PR whenever a vendored skill or standards doc drifts from the template.
It needs a `TEMPLATE_SYNC_TOKEN` repo secret with `Contents:read` on the template
(to read it) **and** `pull-requests:write` on this repo (to open the drift PR —
the default `GITHUB_TOKEN` is refused at PR creation by the "Allow Actions to
create PRs" setting). A classic PAT with the `repo` scope covers both.
