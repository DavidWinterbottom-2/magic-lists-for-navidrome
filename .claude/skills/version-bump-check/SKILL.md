---
name: version-bump-check
description: >-
  Add a per-PR version-bump check to a repo — a CI job that fails unless the
  component's version (its single source of truth) was bumped in the PR,
  bumped with a stack-appropriate tool. Use when a repo has a declared version
  but no CI enforcement of a bump (REPO-STANDARDS §2), or when setting up
  versioning for a new library/app.
---

# Per-PR version-bump check

REPO-STANDARDS §2 requires that **every PR bumps the component's
version** in its single source of truth, using the right tool for the stack, and that **CI
fails the PR if it wasn't bumped**. Pure `:latest`-deployed services (versioned by image
SHA) are exempt.

The check fires on **every** PR — it does not try to work out which PRs "change behaviour".
That is deliberate: over-bumping a docs- or CI-only PR is harmless (images deploy by SHA),
while a path filter that guesses what ships is fragile and can let a real change merge
un-bumped. Keep it simple and unconditional (see the Notes).

## Pick the stack-appropriate tool

| Stack | Version source | Bump command |
| --- | --- | --- |
| JS / TS | `package.json` | `npm version patch\|minor\|major` (or [Changesets]) |
| Python | `pyproject.toml` | `bump-my-version bump patch`, `hatch version`, or `poetry version` |
| Rust | `Cargo.toml` | `cargo set-version --bump patch` (cargo-edit) |
| Go | git tag | tag the release `vMAJOR.MINOR.PATCH` (no version file) |

Never hand-edit the number — the tool keeps lockfiles/tags consistent.

## The CI check (fails when the version didn't change)

Drop this into `.github/workflows/version-check.yml`. It compares the version source against
the PR's base branch and fails if it's unchanged. Example for a Python `pyproject.toml`
(swap the file/parse for your stack):

```yaml
name: version-check
on: pull_request
jobs:
  version-bumped:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - name: Fail if the version wasn't bumped
        run: |
          base="origin/${{ github.base_ref }}"
          git fetch origin "${{ github.base_ref }}" --depth=1
          new=$(grep -m1 '^version' pyproject.toml | tr -d ' "' | cut -d= -f2)
          old=$(git show "$base:pyproject.toml" | grep -m1 '^version' | tr -d ' "' | cut -d= -f2)
          echo "old=$old new=$new"
          if [ "$old" = "$new" ]; then
            echo "::error::version not bumped ($new). Bump it with your stack's tool." && exit 1
          fi
```

For JS use `jq -r .version package.json`; for Rust parse `Cargo.toml`. Make the check
**required** in branch protection so it actually gates merges (§1).

## Notes

- **Don't make the check conditional.** Fire on every PR; do not add path filters or
  "skip the bump when only docs / CI / non-shipping files changed" logic. It couples the
  check to the Dockerfile, drifts as the layout changes, and — worst — can silently let a
  real change merge un-bumped. Over-bumping a non-shipping PR costs nothing (`:latest`
  images deploy by SHA); a fragile path list does. One simple, unconditional check is the
  fleet standard.
- A repo that deliberately doesn't version (a throwaway spike, or a pure `:latest` service)
  can silence Hermes's finding with a `.hermes-ignore` line `§2 Versioning`.
- This is the human/Claude-run counterpart to Hermes's `No version-bump check in CI` finding.
