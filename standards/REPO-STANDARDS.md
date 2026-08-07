# REPO-STANDARDS

**Version:** 1.14-seed · **Owner:** Hermes (self-hosted standards & skills agent)

> **Companion:** deployment-layer security — how a *hosted* service is exposed and how access
> to it is controlled — lives in [`HOSTING-SECURITY.md`](HOSTING-SECURITY.md). This doc
> measures a **repo**; that one measures a **running service**. A first-party deployed service
> (§6) is subject to both.

The engineering standards every winterbottom.xyz repo is measured against. This is the
**rubric Hermes uses** — it reads a live checkout of each repo, compares it to the sections
below, and proposes fixes as GitHub issues (it never merges anything itself).

> **Seed vs derived.** This v1.0 is *seeded* from the conventions already visible in the
> org's repos (see `docker-infra/CONTRIBUTING.md`, the CI workflows, and the per-service
> README discipline) plus widely-accepted gaps. Hermes refines it on each run: where a
> majority of repos already do something better, that becomes the standard; where a common
> practice is a gap, it's flagged as one even though it's common. Change the version and add
> a dated note under **Change log** when a rule is adopted or amended.

Each section is checkable. Mechanical rules are enforced by Hermes's heuristics; the rest are
reviewed by the model against the repo's actual contents.

---

## §1 Branch protection

- **The default branch is `main`** — every repo uses the same name (rename any `master`).
- `main` is protected: no direct pushes; all changes land via pull request.
- A PR requires **at least 1 approval** and **all status checks green** before merge.
- Branches must be **up to date with `main`** before merging; history is preserved
  (no force-push to `main`).
- Work happens on feature branches, named for **who** created them and **why**:
  - **Claude following a skill** — `claude/<skill-name>/<description>`
    (e.g. `claude/design-system-sync/token-refresh`), so the branch records which
    skill drove the change.
  - **Claude resolving an OpenSpec change** — `openspec/<change-id>`, where the
    change id *is* the description (e.g. `openspec/add-audio-export`).
  - **Bug fixes** — `bugfix/<description>` (e.g. `bugfix/pdf-unicode-crash`),
    whoever makes them.
  - **Claude, ad-hoc** (no skill, no OpenSpec change) — `claude/<description>-<id>`
    (e.g. `claude/whatsapp-sentiment-analysis-0ss56y`).
  - **Human work** — `<type>/<description>` (`feat`, `chore`, `docs`, …).
- **Checkable:** the default branch is `main`, is **protected** (a ruleset or classic branch
  protection), and **requires a pull request** to merge — Hermes verifies this via the GitHub
  API. CI is configured (`.github/workflows/`) so there is a status check to require.
- **Enforcement caveat:** GitHub only *enforces* branch protection / rulesets on **private**
  repos under a **paid plan** (Pro/Team/Enterprise) or when the repo is **public**. On a
  private Free-plan repo the rule can be configured but is not enforced. Where that's the
  case, protection remains the **required target** — configure the ruleset anyway so it
  activates on upgrade/publish — and the PR-only workflow is upheld by convention. Hermes
  treats §1 as an **accepted deviation** (not a failure) for a private Free-plan repo whose
  protection is otherwise correctly configured and documented; such repos opt out with a
  `.hermes-ignore` line `§1 Branch protection`.

## §2 Versioning & releases

- **First-party deployed services** are consumed by docker-infra as GHCR images tagged
  `:latest` and updated by Watchtower; every image build is also tagged with the commit SHA
  for traceability and rollback.
- **Third-party deployed services** (upstream images, no org repo — see §6) are versioned by
  their **upstream image tag**. Prefer an explicit or overridable tag
  (`image: foo:${FOO_VERSION:-<version>}`, or a hard pin) over a bare `:latest`, so
  upgrades are deliberate and rollbackable; record notable upgrades. There is no per-PR
  version bump to enforce (there is no repo).
- **Libraries / published artifacts** use semantic versioning (`MAJOR.MINOR.PATCH`) and a
  single source of truth for the version (e.g. `pyproject.toml` / `package.json`).
- **Every PR bumps the component's version** in that single source
  of truth, chosen correctly (patch/minor/major), using a **stack-appropriate tool** rather
  than hand-editing:
  - JS/TS — `npm version` (or Changesets); Python — `bump-my-version` / `hatch version` /
    `poetry version`; Rust — `cargo set-version`; Go — a semver git tag.
- **Checkable:** CI has a **check that fails a PR when the version wasn't bumped**, run on
  **every** PR (diff the version source against the base branch). The check deliberately
  fires on every PR rather than trying to detect which PRs "change behaviour": over-bumping a
  docs- or CI-only PR is harmless (deployed images are pulled by SHA), whereas a path filter
  that guesses what ships is fragile, drifts from the Dockerfile, and can let a real change
  merge un-bumped — so keep the check simple and unconditional. Pure `:latest`-deployed
  services are versioned by their image SHA and are exempt; libraries and versioned apps are
  not.
- **Multi-component repos** — a monorepo where each independently-deployed component
  (service, server, sidecar) carries its **own** version source — satisfy this **per
  component**: each component's check runs on the PRs that touch that component and fails an
  un-bumped change to it. That per-component scoping **is** the correct implementation here —
  it is *not* the "fragile path filter" the bullet above warns against (that warning is about
  a **single-version** repo trying to guess what ships). A repo whose version checks are
  already scoped per component therefore **satisfies §2**; do **not** report the presence,
  absence, or shape of that scoping as a gap. A PR that touches **no** component (docs- or
  CI-only) simply has nothing to bump; a single-version repo has no components to scope to, so
  its one check stays unconditional (above).
- Notable changes are recorded — a `CHANGELOG`, GitHub Releases, or a dated **Updates**
  section in the README (the pattern docker-infra services already use).

## §3 PR standards

- PR descriptions follow **Summary / Changes / Validation**: what changed and why, the files
  touched, and how it was verified (`docker compose config`, health checks, tests).
- **Use the shared PR template.** Every repo carries `.github/pull_request_template.md` — the
  canonical **Summary / Changes / Validation** skeleton. GitHub pre-fills it into any PR opened
  in the web UI, and the `create-pr` skill fills the same file for Claude-authored PRs, so one
  house style covers both paths. The template is org-owned: it ships from the
  `devcontainer-sandbox` template and propagates via `template-sync` — don't hand-roll a
  divergent per-repo copy; amend the shared one.
- One logical change per PR; keep diffs reviewable.
- When a PR merges, add a dated entry to the affected service/component **README Updates**
  section, linking the PR.
- **Checkable:** the repo has `.github/pull_request_template.md` (GitHub's detected path), and
  documents its conventions in a `CLAUDE.md` / `AGENTS.md` so agents and humans follow house
  style.

## §4 Testing

- **Unit tests** cover core logic and run fast; they are the default bar for any behaviour
  change.
- **Integration tests** cover the seams that matter (DB, HTTP, external APIs) — mock the
  network in unit tests, exercise it in integration.
- **End-to-end / smoke tests** validate the deployable path (for services, at minimum
  `docker compose config` + a `/health` check).
- **Apps with a GUI have browser-driven e2e tests.** Any repo that serves a web UI (the same
  `has_web_ui` signal §8 uses) must have **end-to-end tests that drive the actual UI in a
  browser** — loading the app, exercising its primary user flow(s), and asserting on rendered
  results — not just API/unit coverage. Use a browser automation tool (e.g. Playwright, which
  is pre-provisioned in the devcontainer) and run it headless in CI.
- **Coverage — at least 80%.** Unit-test coverage of the component's core code must be
  **≥ 80%**, measured by the stack's coverage tool and **enforced in CI** so a PR that drops
  below the threshold fails the build — not merely reported. Use the stack-appropriate gate:
  Python — `pytest --cov=src --cov-fail-under=80` (as `asset-overview` already runs); JS/TS —
  `jest --coverage` with a `coverageThreshold` of 80 (or the runner's equivalent); Go —
  `go test -coverprofile` gated at 80%. 80% is the floor, not the target — raise it where a
  component warrants it, never lower it.
- **Checkable:** the repo contains tests **and** CI actually runs them on every PR. CI that
  builds but never runs a test command does not satisfy this. The test run must include a
  **coverage gate at ≥ 80%** (a `--cov-fail-under` / `coverageThreshold`-style check that
  fails the PR) — Hermes flags a repo whose CI runs tests without a coverage floor. For a
  web-UI repo, that test run must also include the browser-driven e2e suite — Hermes flags a
  GUI app whose CI has no e2e run.

## §5 Refactoring & cleanup

- Leave code better than you found it: no dead code, commented-out blocks, or unused deps in
  a merged change.
- Prefer reusing an existing utility/skill over adding a near-duplicate; consolidate when two
  modules drift into the same responsibility.
- Refactors that change behaviour need test coverage proving behaviour is unchanged.
- Secrets never land in the repo — use `.env` (git-ignored) with a committed `.env.example`.

## §6 Repo hygiene (baseline)

Every repo should have:

- a **name in lowercase kebab-case** — lowercase letters, digits and single hyphens
  only, matching `^[a-z0-9]+(-[a-z0-9]+)*$`: no uppercase, spaces, underscores or other
  special characters, and **no leading or trailing hyphen**. (Watch for a stray trailing
  space in the name — GitHub silently turns it into a trailing hyphen, e.g.
  `Nutrition-Tracker `→`Nutrition-Tracker-`.) It should also match the service/image name
  it maps to (`ghcr.io/davidwinterbottom-2/<name>`). Rename via GitHub Settings → General;
  GitHub redirects the old URL, then fix the in-repo references. **Checkable:** the repo
  name matches the pattern above;
- a top-level **README** describing what it is, how to run it, and its configuration;
- a **LICENSE** (or an explicit private / all-rights-reserved statement);
- a **`.gitignore`** appropriate to the stack, with `.env` ignored and `.env.example`
  committed;
- a **`CLAUDE.md` / `AGENTS.md`** capturing branch/PR workflow, testing and layout
  conventions;
- for a deployed service: a `docker-compose.yml` with a healthcheck, a committed
  `.env.example`, and a `Makefile` exposing the **standard service targets** so every
  service is operated the same way:
  `help · install · start · stop · restart · logs · status · health · clean · pull · config`.

**Two classes of deployed service.** The rules differ by where the image comes from:

- **First-party (locally built)** — built from an **org repo** and published as
  `ghcr.io/davidwinterbottom-2/<name>:latest` (Watchtower auto-updates; the running
  version is the image SHA). The **full repo standards (§1–§9) apply to that source repo**;
  the `docker-infra` service directory is only the deployment (compose + Makefile + README +
  `.env.example`). Versioning follows §2 for that repo (a pure `:latest` app is versioned by
  its image SHA; an app that also declares a version source enforces the per-PR bump).
- **Third-party (pulled)** — runs an **upstream image** (grafana, the `*arr` apps, kafka,
  navidrome, …) with **no org repo**. The repo-level sections (§1 branch protection,
  §3 in-repo PR conventions, §7 devcontainer, §9 review cadence) **do not apply** — there is
  no repo to apply them to. What applies is the service-directory hygiene above **plus
  image-tag discipline**: prefer a pinned or pinnable tag — the
  `image: foo:${FOO_VERSION:-<version>}` knob pattern already used by grafana / kafka /
  the `*arr`s, or a hard pin like `kafka:3.9.1` — over a bare `:latest`, so upgrades are
  deliberate and rollbackable, and record notable upgrades (§2). There is no per-PR version
  bump to enforce.

**Service Makefile — shared includes.** Don't hand-roll the target boilerplate. A service's
`Makefile` sets a few variables and `include`s the canonical template for its kind — both
live in `docker-infra/home-docker/services/`:

- **first-party web apps** (the org's own `ghcr.io` image) → **`service-common.mk`**: set
  `SERVICE_NAME` and `SERVICE_PORT` (plus optional knobs — `SERVICE_DESC`,
  `SERVICE_DATA_DIRS`, `SERVICE_REQUIRE_ENV`, `SERVICE_CLEAN_VOLUMES`,
  `SERVICE_CLEAN_IMAGES`, `SERVICE_EXTRA_HELP`, or `CUSTOM_INSTALL` for a bespoke install),
  then `include ../service-common.mk`.
- **MCP servers** (also first-party) → **`mcp-common.mk`**: set `SERVICE_NAME` /
  `SERVICE_PORT` and include it.
- **third-party services** with genuinely bespoke operations may keep a **standalone
  `Makefile`** — their upstream image, config and health checks vary too much for a shared
  template — but should still expose the same target names.

New services get this wired by the `new-service-checklist` / `promote-to-service` skills.

## §7 Dev environment (devcontainer)

- Every project is templated off
  [`devcontainer-sandbox`](https://github.com/DavidWinterbottom-2/devcontainer-sandbox) so
  the code runs in a **VS Code devcontainer / Codespaces** — a one-command, reproducible dev
  environment rather than per-machine setup.
- **Checkable:** the repo has a `.devcontainer/` (a `.devcontainer/devcontainer.json`, or a
  root `.devcontainer.json`). Keep it aligned with the sandbox template as that evolves.
- The **shared skills library** lives in the template's `.claude/skills/` and syncs down to
  every repo via `.github/workflows/template-sync.yml`; new skills are proposed *up* to the
  template. (This doc — the *policy* — lives here in `devcontainer-sandbox/standards/`,
  alongside the shared skills library.)
- Config-only repos with no application code (e.g. docker-infra itself) don't need a
  devcontainer; they opt out with a `.hermes-ignore` line `§7 Dev environment`.

## §8 Design system

- Every winterbottom.xyz **web UI** shares one look — the **winterbottom design system**.
  The canonical assets and the doc live in
  [`docker-infra/design-system/`](https://github.com/DavidWinterbottom-2/docker-infra/tree/main/design-system):
  `winterbottom.css`, `winterbottom-theme.js`, `LOOK-AND-FEEL.md`, and a live
  `style-guide.html`.
- Apps **vendor** the two shared files (`winterbottom.css` + `winterbottom-theme.js`) and
  style through the design **tokens** rather than hand-rolling colours, spacing or type. Keep
  the vendored copies in sync as the system evolves (see the `design-system-sync` skill).
- **Checkable:** a repo that serves HTML (a web UI) has `winterbottom.css` /
  `winterbottom-theme.js` present. Hermes flags a web UI that doesn't — detected via the
  `has_web_ui` / `uses_design_system` signals and paired with the `design-system-sync` skill.
- `docker-infra` **owns** the source assets and is exempt: it opts out with a `.hermes-ignore`
  line `§8 Design system`. Repos with no web UI aren't candidates.

## §9 Review cadence

Reviews are **run by a capable agent** (Claude Code, via the `code-review` and
`improve-codebase-architecture` skills) — **not** by Hermes. Hermes's job is to track that
they happen on cadence. The evidence is a **committed review log** the reviewer updates.

- **Code review** — run the `code-review` skill on **every non-trivial PR**, and at least
  **weekly** on the repo's active work.
- **Architecture review** — run an architecture review (`improve-codebase-architecture`)
  **after any significant change** (a new module/service, a data-model change, a refactor
  touching many files) and at least **monthly** on an actively-developed repo.
- **Record every review** in [`docs/reviews/LOG.md`](../docs/reviews/LOG.md), newest first,
  one line per review (use the `record-review` skill so the format stays machine-readable):

  ```
  - YYYY-MM-DD | <type> | <scope> | <note or PR link>
  ```

  where `<type>` is `code-review` or `architecture-review`. Example:

  ```
  - 2026-07-24 | code-review | PR #42 | standards + spec, 2 findings fixed
  - 2026-07-20 | architecture-review | ingestor module | extracted client seam
  ```

- **Checkable:** Hermes reads `docs/reviews/LOG.md`, takes the newest date per type, and
  flags a **missing log**, a **review never recorded**, or one that's **overdue** against the
  cadence windows (7 days for code review, 30 for architecture) — via the `has_review_log` /
  `last_code_review` / `last_architecture_review` signals. Hermes never runs the review
  itself; a `.hermes-ignore` line `§9 Review cadence` opts out repos with no application code.

## §10 Linting & formatting

- **Every file is linted and formatted** by a stack-appropriate tool whose config is committed
  to the repo — code style is a machine's job, not a matter of editor settings or review
  nitpicks. Both a **linter** (catches bugs and bad patterns) and a **formatter** (enforces
  consistent layout) apply.
- Use the standard tool for each language, configured in-repo:
  - **Python** — `ruff` (`ruff check` for lint + `ruff format --check` for format), as
    `asset-overview` already does.
  - **JS/TS** — **ESLint** + **Prettier** (`prettier --check`), the pair already wired into the
    devcontainer.
  - **Shell** — `shellcheck`; **Dockerfiles** — `hadolint`; **YAML / Markdown / JSON** — a
    linter/formatter (`prettier`, `yamllint`) where practical.
- **Format is checked, not silently applied.** CI runs the formatter in **check mode**
  (`ruff format --check`, `prettier --check`) so an unformatted file **fails the PR** rather
  than being reformatted behind the author's back.
- **Enforced in CI on every PR.** The lint + format-check commands run as a CI job and fail the
  build on any violation — a linter that only runs locally does not satisfy this.
- **Checkable:** the repo has a lint/format config (`[tool.ruff]`, `.eslintrc*` /
  `eslint.config.*`, `.prettierrc*`, …) **and** a CI job that runs the lint and format-check
  commands on every PR. Hermes flags a repo with source files but no CI lint/format gate.

## §11 Usage analytics

- Every winterbottom.xyz **web UI** should report anonymous usage to the shared self-hosted
  **[Umami](https://umami.winterbottom.xyz)** — the same privacy-first, cookieless collector
  the other apps use — so feature usage is visible without a third-party tracker. One Umami
  **website per app**.
- Analytics is wired through a fixed **env contract**, never hard-coded: the app reads
  `ANALYTICS_SCRIPT_URL` and `ANALYTICS_WEBSITE_ID` from the environment and injects the Umami
  `<script>` **only when both are set**. It is **off by default** and collects nothing until a
  collector you run is configured (a half-configured pair is treated as off). `.env.example`
  documents both knobs.
- **First-party only.** A web UI must not ship a hard-coded or third-party analytics tag
  (Google Analytics, a Meta Pixel, any hosted SDK). This is the repo-level face of the
  deployment-layer rule in [`HOSTING-SECURITY.md`](HOSTING-SECURITY.md) **§H5** (self-hosted,
  cookieless, first-party): at the repo level it means the env-gated contract above and no
  hosted third-party tracker. Firm even where wiring Umami itself is optional.
- **Checkable:** a repo that serves HTML injects the Umami tag via the
  `ANALYTICS_SCRIPT_URL` / `ANALYTICS_WEBSITE_ID` contract (off by default) and carries no
  hard-coded or third-party tracker. Hermes reviews a web UI against this and flags a
  hard-coded/third-party tag or, informationally, one that wires no analytics at all — the
  `has_analytics` signal, mirroring §8's `has_web_ui`. `docker-infra` owns the collector and
  is exempt.

---

## Change log

- **2026-08-04 — v1.14-seed.** Added **§11 Usage analytics**: every web UI should report
  anonymous usage to the shared self-hosted **Umami** through a fixed, **off-by-default** env
  contract (`ANALYTICS_SCRIPT_URL` + `ANALYTICS_WEBSITE_ID`), one website per app — and **must
  not** ship a hard-coded or third-party analytics tag. Reviewed against web-UI repos (the
  `has_analytics` signal, mirroring §8's `has_web_ui`) the deployment-layer
  companion is `HOSTING-SECURITY.md` §H5, and it is paired with the app-side analytics
  wiring already shipped across the winterbottom apps.

- **2026-07-27 — v1.13-seed.** Added a companion standard, **[`HOSTING-SECURITY.md`](HOSTING-SECURITY.md)**,
  for the **deployment layer** — how a *hosted* service is exposed and how access to it is
  controlled — a concern distinct from this per-repo rubric. It codifies existing infra
  practice: every internet-reachable (`public`) service authenticates — or is deliberately
  anonymous with a recorded rationale (§H1); auth is Microsoft/Entra via one of a fixed
  vocabulary — `entra-app` / `entra-proxy` / `oauth` / `app-native` / `mcp`, plus `anon` for a
  justified no-sensitive-data / no-internal-access public service (§H2); cross-cutting sidecars (the `entra-auth-proxy`) live in
  `sidecar-containers` and are consumed as pinned images (§H3); one public TLS edge with
  secrets from `.env`, plus a recorded-waiver escape hatch (§H4). Enforced at the deployment
  layer by `docker-infra/scripts/check-hosting-security.py`, which extends `tools-index.json`
  with a per-public-service `auth` field. It applies to *hosted* services (first- and
  third-party), not development-only artefacts.
- **2026-07-25 — v1.12-seed.** §3: require the **shared PR template**. Every repo carries
  `.github/pull_request_template.md` (GitHub's detected path) — the canonical
  Summary / Changes / Validation skeleton that GitHub pre-fills for web-UI PRs and the new
  `create-pr` skill fills for Claude-authored PRs. It's org-owned: ships from the
  `devcontainer-sandbox` template and propagates via `template-sync`, so repos share one house
  style instead of hand-rolled per-repo copies. Presence of the file is the checkable signal.
- **2026-07-24 — v1.11-seed.** Added **§10 Linting & formatting** and a **coverage floor to
  §4**. §10: every file must be linted **and** formatted by a stack-appropriate, in-repo tool
  (Python `ruff check` + `ruff format --check`; JS/TS ESLint + Prettier `--check`; shell
  `shellcheck`; Dockerfiles `hadolint`; …), with the checks **run in CI on every PR** in check
  mode so unformatted or lint-failing files fail the build. §4: unit-test coverage of core code
  must be **≥ 80%**, enforced by a CI coverage gate (`--cov-fail-under=80` /
  `coverageThreshold`) that fails a PR dropping below the floor — not merely reported. Both are
  derived from existing org practice: `asset-overview` already runs `ruff` and
  `pytest --cov-fail-under=80`, and the devcontainer ships ESLint + Prettier.
- **2026-07-24 — v1.10-seed.** §4: require **browser-driven e2e tests for any app with a
  GUI**. A repo that serves a web UI (the `has_web_ui` signal, as in §8) must have end-to-end
  tests that drive the real UI in a browser through its primary user flow(s) — not just
  unit/API coverage — run headless in CI (Playwright is pre-provisioned in the devcontainer).
  Hermes flags a GUI app whose CI has no e2e run.
- **2026-07-24 — v1.9-seed.** §6: added a **repo naming rule** — repo names must be
  lowercase kebab-case (`^[a-z0-9]+(-[a-z0-9]+)*$`): no uppercase, spaces, underscores,
  other special characters, or leading/trailing hyphen, and should match the
  service/image name. Prompted by `Nutrition-Tracker-`, whose trailing hyphen came from a
  stray trailing space in the original name (GitHub converts a trailing space to a hyphen).
  Checkable mechanically by Hermes against the pattern.
- **2026-07-24 — v1.8-seed.** §6 + §2: distinguished **two classes of deployed service**.
  *First-party (locally built)* services have an org repo and take the full repo standards
  (§1–§9) plus `service-common.mk` / `mcp-common.mk`; *third-party (pulled)* services run an
  upstream image with no repo, so the repo-level sections don't apply — they follow the
  service-directory hygiene and **image-tag pinning** (prefer `foo:${FOO_VERSION:-X}` or a
  hard pin over bare `:latest`) and may keep a bespoke standalone Makefile.
- **2026-07-24 — v1.7-seed.** §6: codified the **service Makefile**. The standard target
  set (`help install start stop restart logs status health clean pull config`) is now
  provided by shared includes in `docker-infra/home-docker/services/` — `service-common.mk`
  for first-party web apps, `mcp-common.mk` for MCP servers — that a service `include`s
  after setting `SERVICE_NAME`/`SERVICE_PORT` (plus optional knobs). Existing first-party
  services were migrated onto it; third-party-image services may keep a standalone Makefile
  but expose the same targets. Wired into `new-service-checklist` / `promote-to-service`.
- **2026-07-24 — v1.6-seed.** §1: added an **enforcement caveat**. GitHub only enforces
  branch protection / rulesets on private repos under a paid plan or when public; on a
  private Free-plan repo the ruleset is configured but not enforced. Protection stays the
  required target (configured so it activates on upgrade/publish) and the PR-only workflow is
  upheld by convention; Hermes treats §1 as an accepted deviation for such repos, which opt
  out via a `.hermes-ignore` line `§1 Branch protection`.
- **2026-07-24 — v1.5-seed.** Added §9 Review cadence: code review weekly / per-PR and
  architecture review after significant changes (and monthly), *run by Claude Code* and
  recorded in `docs/reviews/LOG.md`. Hermes audits the cadence from the log (it doesn't run
  reviews) via the `has_review_log` / `last_code_review` / `last_architecture_review`
  signals, paired with the `record-review` skill; config-only repos opt out via
  `.hermes-ignore`.
- **2026-07-24 — v1.4-seed.** Added §8 Design system: every web UI vendors the shared
  winterbottom design-system assets (`winterbottom.css` / `winterbottom-theme.js` from
  `docker-infra/design-system`) and styles through its tokens. Detected by Hermes
  (`has_web_ui` / `uses_design_system` signals) and paired with the `design-system-sync`
  skill; `docker-infra` owns the source and opts out via `.hermes-ignore`.
- **2026-07-23 — v1.3-seed.** The shared skills library moved to the `devcontainer-sandbox`
  template (`.claude/skills/`), synced down to repos by `template-sync.yml`; Hermes proposes
  new skills *up* to the template and dedups against it. §7: config-only repos opt out of the
  devcontainer requirement via `.hermes-ignore`.
- **2026-07-23 — v1.2-seed.** Added §7 Dev environment: every project is templated off
  `devcontainer-sandbox` so it runs in a VS Code devcontainer. Detected by Hermes
  (`has_devcontainer` signal) and paired with the `devcontainer-from-sandbox` skill.
- **2026-07-23 — v1.1-seed.** §2: require a per-PR version bump via a stack-appropriate tool,
  plus a CI check that fails when the version wasn't bumped (`:latest` services exempt).
  Detected mechanically by Hermes (`has_version` / `ci_checks_version` signals) and paired
  with the `version-bump-check` skill.
- **2026-07-22 — v1.0-seed.** Initial seed, derived from existing org conventions
  (`docker-infra/CONTRIBUTING.md`, service README/`.env.example` discipline, tools-index
  governance) plus baseline testing/hygiene gaps. Hermes to refine on first live run.
