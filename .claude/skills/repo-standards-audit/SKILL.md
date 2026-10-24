---
name: repo-standards-audit
description: >-
  Audit one or more repos against standards/REPO-STANDARDS.md and report drift
  (audit mode), or derive/refine the standards from what the repos already do
  (derive mode). Use when reviewing a repo's engineering hygiene — CI, tests,
  branch protection, PR conventions, repo baseline — or when updating the
  canonical standards doc. This is the human-run counterpart to the Hermes agent.
---

# Repo standards audit

The canonical rubric is [`standards/REPO-STANDARDS.md`](../../../standards/REPO-STANDARDS.md)
(§1 Branch protection, §2 Versioning, §3 PR standards, §4 Testing, §5 Refactoring, §6 Repo
hygiene, §7 Dev environment, §8 Design system, §9 Review cadence, §10 Linting & formatting).
This skill runs the same comparison the [Hermes](https://github.com/DavidWinterbottom-2/hermes)
agent automates, but on demand in a Claude Code session.

## Audit mode (default)

For each target repo, read the **live** checkout (never assume) and check it against every
section. Gather these signals first:

- repo **name** in lowercase kebab-case, matching `^[a-z0-9]+(-[a-z0-9]+)*$` (no uppercase,
  spaces, underscores, special chars, or leading/trailing hyphen)? (§6)
- `README`, `LICENSE`, `.gitignore`, `.env.example` present? (§6)
- `CLAUDE.md` / `AGENTS.md` present? (§3, §6)
- `.github/workflows/*` — is CI configured, and does any job actually run tests
  (`pytest` / `jest` / `go test` / …)? (§1, §4)
- tests present at all, and does CI enforce a **coverage gate at ≥ 80%**
  (`--cov-fail-under` / `coverageThreshold`)? (§4)
- lint/format tool configured in-repo (`[tool.ruff]`, `.eslintrc*`, `.prettierrc*`, …) **and**
  a CI job that runs lint + format-check on every PR? (§10)
- version source of truth + change history? (§2)

Report findings as a table: **severity · section · finding · suggested action**. Be
conservative — only flag what the repo contents justify. Distinguish:

- **standards-drift** — the repo violates a rule it should follow;
- **missing-skill** — a reusable playbook that would help this repo but isn't in the library;
- **new-skill** — a good pattern this repo already does that others should adopt.

Propose new skills as draft `SKILL.md` files; don't merge anything — open an issue / PR for
review.

## Derive mode

Use when building or refining `REPO-STANDARDS.md` from reality rather than imposing rules.
For each rule, find the **majority pattern** across the repos:

- If most repos already do the stronger thing, adopt it as the standard.
- If a common practice is actually a gap, record it as a gap even though it's common
  ("adopt this, it's better" vs "gap, even if common").
- On ties or genuine ambiguity, flag it for a human decision rather than guessing.

Output a proposed diff to `REPO-STANDARDS.md`, bump the version, and add a dated **Change
log** entry describing what was adopted or amended.

## Notes

- The rubric and the skills library are the source of truth and live in **docker-infra**.
- Keep section headings stable (`§1 …` … `§10 …`) — Hermes's heuristics reference them.
