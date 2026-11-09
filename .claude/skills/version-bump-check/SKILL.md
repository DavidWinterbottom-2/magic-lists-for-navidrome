---
name: version-bump-check
description: >-
  Add (or retrofit) a per-PR version-bump check to a repo — a CI job that fails
  unless the component's version (its single source of truth) is strictly
  greater, by semver, than the base branch's, except on docs-only PRs — and the matching CI gating that skips heavy jobs
  on docs-only and draft PRs to save Actions minutes. Use when a repo has a
  declared version but no CI enforcement of a bump (REPO-STANDARDS §2), when
  setting up versioning for a new library/app, or when a private repo's
  Actions minutes are being wasted.
---

# Per-PR version-bump check (docs-only exempt) + minute-saving CI

REPO-STANDARDS §2 requires that **every PR that changes what ships bumps the
component's version** in its single source of truth, using the right tool for the
stack, and that **CI fails the PR if it wasn't bumped**. **Docs-only PRs are
exempt.** Pure `:latest`-deployed services (versioned by image SHA) are exempt,
but `issue-stages` stage 7's deploy gates need a declared version, so a deployed
first-party service should declare one.

The exemption and the CI gating below belong together. Private repos pay per
job-minute (each job rounds up to a whole minute). A forced bump edits the version
file, which is code, so without the exemption a docs PR still runs the full suite.

Do this as **one focused PR** on a new branch, in a fresh session, not one that is
mid-PR. Don't edit workflows that `template-sync.yml` copies from the template
(`issue-linked.yml`, `labels.yml`, `template-sync.yml`). They are overwritten, so change
them in devcontainer-sandbox.

## Pick the stack-appropriate tool

| Stack | Version source | Bump command |
| --- | --- | --- |
| JS / TS | `package.json` | `npm version patch\|minor\|major` (or [Changesets]) |
| Python | `pyproject.toml` | `bump-my-version bump patch`, `hatch version`, or `poetry version` |
| Rust | `Cargo.toml` | `cargo set-version --bump patch` (cargo-edit) |
| Go | git tag | tag the release `vMAJOR.MINOR.PATCH` (no version file) |

Never hand-edit the number. The tool keeps lockfiles and tags consistent.

## 1. One definition of "docs-only": `.github/scripts/code-changed.sh`

Both CI and version-check call this script. On a `pull_request` checkout, `HEAD`
is the merge commit and `HEAD^1` is the base tip, so `git diff --name-only HEAD^1 HEAD`
(with checkout `fetch-depth: 2` or more) is exactly the PR's diff. It needs no
third-party action. The script prints `code=true|false`; a multi-component repo
also prints one flag per component, e.g. `web=true`, `mcp=false`.

**Allowlist docs; everything else is code**, so a mistake errs towards running CI.
A changed file is docs-only when it is `*.md`, under `docs/`, `openspec/` or
`.claude/`, or `LICENSE` — **and** it doesn't ship and nothing executes it. Read
each `Dockerfile` and its `.dockerignore` to decide whether a file ships:

- **Explicit COPY/ADD of named folders** (`COPY src/ ./src/`): a doc outside every
  copied folder doesn't ship, even under `containers/<name>/`. A doc *inside* a
  copied folder (e.g. `templates/help.md`) ships, so it is code.
- **`COPY . .` / a whole-context copy**: everything in the context ships. Add
  `**/*.md` and `docs/` to that container's `.dockerignore`, check that the build
  and the app don't need them, and rebuild the image to prove it. Only then treat
  them as docs.
- **Anything a test, the app or a build step reads** (grep for the filename and
  its folder) is code, wherever it lives.
- **`.github/**` is always code**, because workflow changes must exercise CI.

Comment each rule in the script with the Dockerfile that justifies it. Reference
implementation (one container, explicit COPY):

```bash
#!/usr/bin/env bash
set -euo pipefail
base="${1:-HEAD^1}"; head="${2:-HEAD}"
code=false
while IFS= read -r path; do
  [ -z "$path" ] && continue
  case "$path" in
    .github/*) code=true ;;     # workflow changes must exercise CI
    containers/*) code=true ;;  # the app: anything here may ship in the image
    *.md | docs/* | openspec/* | .claude/* | LICENSE) ;;
    *) code=true ;;
  esac
  [ "$code" = true ] && break
done < <(git diff --name-only "$base" "$head")
echo "code=$code"
```

## 2. The version check

`.github/workflows/version-check.yml` fails a code PR unless its version is
**strictly greater, by semver, than the version the base branch declares at its
current tip**. Docs-only PRs are exempt. "Changed" isn't enough: two parallel PRs
can both bump 1.4.2 to 1.4.3, or one can land lower than `main` already is, and a
deploy would then go backwards (#176).

The comparison is `.github/scripts/version-gt.sh A B`: exit 0 if A > B, 1 if
A ≤ B, 2 (naming the value) if either isn't plain `MAJOR.MINOR.PATCH`. No `v`
prefix, no pre-release or build suffix, no leading zeros. It is dependency-free
bash; `sort -V` isn't semver. Copy it verbatim from devcontainer-sandbox's
`template/.github/scripts/version-gt.sh`, where `scripts/test-version-gt.sh`
tests it. Never write your own comparison. Example for a Python `pyproject.toml`
(swap the file and parse for your stack):

```yaml
name: version-check
on: pull_request
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
jobs:
  version-bumped:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - name: Fail unless the version is greater than the base's
        env:
          BASE: ${{ github.base_ref }}
        run: |
          if .github/scripts/code-changed.sh | grep -qx 'code=false'; then
            echo "docs-only change: no bump required" && exit 0
          fi
          base="origin/$BASE"
          git fetch origin "$BASE" --depth=1
          new=$(grep -m1 '^version' pyproject.toml | tr -d ' "' | cut -d= -f2)
          old=$(git show "$base:pyproject.toml" | grep -m1 '^version' | tr -d ' "' | cut -d= -f2)
          rc=0; bash .github/scripts/version-gt.sh "$new" "$old" || rc=$?
          # Values come from the PR's own files: log them with anything outside
          # [0-9A-Za-z.+-] shown as '?', so one can't emit a workflow command.
          show_new=${new//[^0-9A-Za-z.+-]/?}; show_old=${old//[^0-9A-Za-z.+-]/?}
          echo "base=$show_old pr=$show_new"
          if [ "$rc" = 1 ]; then
            echo "::error::version $show_new must be greater than $show_old on $BASE. Bump it with your stack's tool (after merging $BASE in)." && exit 1
          elif [ "$rc" != 0 ]; then
            echo "::error::can't compare versions (pr=$show_new, base=$show_old): both must be plain MAJOR.MINOR.PATCH." && exit 1
          fi
```

This is the same step the copier template renders into `ci.yml`'s
`version-bumped` job; keep the two in step.

- **It depends on §1's up-to-date-branch rule.** The job fetches the base's tip
  when it runs, but a PR whose last run was green before another PR merged the
  same version would still merge. Branch protection's "require branches to be up
  to date" forces the PR to merge `main` in, which reruns this check and catches
  the collision. This skill doesn't enforce that rule; a repo without it
  (REPO-STANDARDS §1, the `configure-branch-protection` skill) is exposed to
  parallel-PR collisions, so say so in the PR.
- **Adopting it is breaking.** Once merged, a PR whose version equals or trails
  `main` fails. Open PRs need `main` merged in and a fresh bump.
- **A version outside `X.Y.Z`** (`1.4.3-rc.1`, `v1.4.3`) fails the check with its
  value named. A repo that needs pre-releases needs its own issue first.

For JS, use `jq -r .version package.json`; for Rust, parse `Cargo.toml`. In a
multi-component repo, a component needs a bump only if its own code changed, and
the check logs one line per component. Make the check **required** in branch
protection so it actually gates merges (§1).

## 3. Gate heavy CI jobs with a job-level `if`, never workflow `paths`

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
concurrency:   # cancel superseded PR runs; push/tag runs never share a group
  group: ${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
  changes:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    timeout-minutes: 5
    outputs:
      code: ${{ steps.diff.outputs.code }}
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 2 }
      - id: diff
        run: .github/scripts/code-changed.sh | tee -a "$GITHUB_OUTPUT"
  test:
    needs: changes
    # !cancelled(): a plain `if:` carries an implicit success(), so a failed
    # `changes` would skip this job, and a skip reports as passing.
    if: >-
      !cancelled() && github.event_name == 'pull_request' &&
      needs.changes.outputs.code != 'false' && !github.event.pull_request.draft
```

- Gate unit tests, e2e, image builds — anything over about a minute. Keep fast lint ungated.
- **Fail safe if `changes` itself fails.** Write the gate as
  `!cancelled() && … && needs.changes.outputs.code != 'false'`, never
  `needs.changes.outputs.code == 'true'`. A plain `if:` carries an implicit
  `success()`, so a failed `changes` job skips every gated job, and a skipped
  job reports as passing: a code PR merges untested. With `!cancelled()` and
  `!= 'false'` (a failed job's outputs are empty), only a definite docs-only
  answer or a draft skips. Keep `github.event_name == 'pull_request'` in the
  gate if those jobs ran on PRs only, or `!cancelled()` starts them on every
  push and tag too. If `changes` also runs on push and answers `code=true`
  there, drop it. Seen in DavidWinterbottom-2/magic-lists-for-navidrome#50.
- **Use a job-level `if`, not workflow `paths`.** A required check that a
  workflow-level `paths`/`paths-ignore` filter leaves out stays "Expected" forever
  and blocks the merge. A *skipped* job reports as passing.
- **Never gate a matrix job this way. Split it into plain jobs.** A matrix
  job that its job-level `if` skips reports a single check under the
  *unexpanded* name, e.g. `Test (${{ matrix.container }})`, instead of
  `Test (web)` / `Test (mcp)`. A required `Test (web)` then stays "Expected"
  on every docs-only and draft PR and blocks the merge. Write one plain job per
  matrix entry, with `name:` set to the exact old check name, so required-check
  names don't change. An ungated matrix (e.g. a fast lint) is fine: it always
  expands. Seen in DavidWinterbottom-2/budget-analyzer#86, fixed in
  DavidWinterbottom-2/budget-analyzer#88.
- **Use `run_id` for push/tag runs.** With a shared group, even
  `cancel-in-progress: false` drops queued runs beyond the one pending, which can
  lose a release build.
- **Drafts** get lint and version-check only. Marking a PR ready runs everything.
- **Timeouts.** Set `timeout-minutes` on every job, at about 2–3× its normal
  duration (5 for trivial jobs, about 30 for multi-arch image builds). Without
  one, a hung job runs for GitHub's 6-hour default. Also set a step-level
  `timeout-minutes` on network installs that are known to hang:
  `playwright install --with-deps` has hung its apt step for 8+ minutes when it
  normally takes about 20 seconds.
- **Caches:**
  - setup-python/node: `cache:` with `cache-dependency-path`.
  - Playwright browsers: `actions/cache` keyed on the *installed* Playwright
    version, not on a file that changes with every bump.
  - Docker: `cache-from/cache-to: type=gha`.
- **Required checks.** Never merge or rename one in this PR, because that blocks
  every open PR until branch protection is updated. List such merges as follow-ups.

## Validate before pushing

- Run `actionlint` on every changed workflow.
- Run the version step against a scratch base/PR pair for equal, lower, greater
  and `1.9.0`→`1.10.0`, and show fail, fail, pass, pass.
- Run `code-changed.sh` against simulated PR merge commits and show the output for:
  - docs-only;
  - code + docs;
  - `.github`-only;
  - a doc *inside* a shipped folder (it must give `code=true`).

  To simulate one in a scratch repo, run `git checkout --detach main && git merge --no-ff <branch>`, then run the script.
- Open the PR as a **draft** first: the gated jobs skip, and each must show as
  *skipped* under its real name (no `${{ matrix.* }}` in a check name). Then
  mark it ready and confirm they all run.
- Run the repo's normal lint and unit tests.
- A PR that changes `.github/**` is code, so bump its version. In a
  multi-component repo with per-component checks, a CI-only PR touches no
  component and has nothing to bump (REPO-STANDARDS §2).
- In the PR description, include a table of each job × {docs-only PR, code PR,
  draft PR, push to main/tag} → runs / skipped (passes).

## Notes

- **Why the docs exemption is safe.** It's an allowlist with "everything else is
  code", checked against each Dockerfile. A mistake errs towards running CI and
  requiring a bump, so a real change can't merge un-bumped because of a missing
  path rule.
- **Template-sync PRs.** A sync that changes only `.claude/**` is docs-only, and
  template-sync never bumps it. In a repo that hasn't adopted the exemption yet,
  `version-bumped` fails on that sync PR. That's deliberate: run this skill in the
  repo to adopt it. A sync that changes `.github/` is code, so template-sync bumps
  the repo's single whole-repo version source; a repo with none (or several) gets
  no bump and must adopt one, or the sync PR fails `version-bumped`.
- **Opting out.** A repo that deliberately doesn't version (a throwaway spike, or
  a pure `:latest` service; a deployed one then can't pass stage 7's gates) can
  silence Hermes's finding with a `.hermes-ignore` line `§2 Versioning`.
- **Shared budget.** The Actions budget is account-wide. Before blaming one repo's
  CI, check Billing → Usage grouped by repository; long-running jobs (e.g. image builds or
  Playwright suites) can dominate.
- This skill is the human/Claude-run counterpart to Hermes's
  `No version-bump check in CI` finding. Reference implementation:
  DavidWinterbottom-2/asset-overview#58.
