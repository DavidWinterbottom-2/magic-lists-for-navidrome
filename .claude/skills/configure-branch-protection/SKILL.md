---
name: configure-branch-protection
description: >-
  Protect a repo's default branch so changes land only via pull request —
  configure a GitHub ruleset (or classic branch protection) that requires a PR,
  at least one approval (none in a solo-maintainer repo), up-to-date branches and green status checks, and blocks
  force-pushes and deletion of `main`. Use when a repo's `main` is unprotected
  (REPO-STANDARDS §1), or when standing up protection on a new repo.
---

# Configure branch protection

REPO-STANDARDS §1 requires that **the default branch is `main`, is protected, and requires a
pull request to merge**. Concretely, `main` must enforce:

- **No direct pushes** — every change lands via PR.
- **At least 1 approving review** before merge — **0** in a solo-maintainer repo (exactly
  one human with write access): GitHub forbids self-approval and a personal account has no
  bypass actor, so 1 would block every merge. Check first:
  `gh api repos/{owner}/{repo}/collaborators --jq '[.[] | select(.type=="User" and .permissions.push)] | length'`.
- **All status checks green** — the repo's CI checks are *required*.
- **Branches up to date with `main`** before merging.
- **History preserved** — no force-push to, and no deletion of, `main`.

Hermes verifies this via the GitHub API, so it must be configured on GitHub — not just
described in a doc.

## 0. Preconditions

- Confirm the default branch is actually named `main` (rename any `master` first, via
  Settings → Branches, then repoint the ruleset).
- There must be **a CI status check to require** (§1 leans on §4). Look in
  `.github/workflows/` for a `pull_request`-triggered job. If CI only builds, wire up a real
  check first (`add-ci-test-steps`) — requiring a check that never runs would block every PR.
  Note the **exact check name** as it appears on a PR (the job name, e.g. `test`).
- Build the required list from the canonical jobs the repo **actually runs on PRs**:
  `version-bumped`, `lint`, `test`, `e2e` and, for a first-party service that ships a GHCR
  image, **`image`** (§4: builds and runs the image, `/health` must report the declared
  version). **Never require a check the repo doesn't run** — it never reports, and every PR
  hangs on "Expected — waiting for status". So:
  - CI has an `image` job → include `image`.
  - The repo ships an image but has **no** `image` job yet → leave `image` out, add the job
    first (copy `asset-overview`'s, or the template's `ships_image` job), let it report
    on a PR, then add `image` here.
  - Never require push-only jobs (`build`, `publish`, deploy): they're skipped on PRs.

  ```bash
  # Each CI job and the context it reports (a job-level `name:` overrides the id):
  python3 -c "import glob, yaml; [print(j, '->', d.get('name', j)) for f in glob.glob('.github/workflows/*.y*ml') for j, d in (yaml.safe_load(open(f)).get('jobs') or {}).items()]"
  ```

## 1. Configure the protection (pick one)

Prefer a **repository ruleset** (GitHub's current mechanism); classic branch protection is
the fallback. Both satisfy §1 — configure one, not both.

### Option A — ruleset via `gh` (recommended)

```bash
gh api --method POST repos/{owner}/{repo}/rulesets \
  -f name='protect-main' -f target='branch' -f enforcement='active' \
  -F 'conditions[ref_name][include][]=~DEFAULT_BRANCH' \
  -F 'conditions[ref_name][exclude][]=' \
  -f 'rules[][type]=deletion' \
  -f 'rules[][type]=non_fast_forward' \
  -f 'rules[][type]=pull_request' \
  -F 'rules[][parameters][required_approving_review_count]=1' \
  -F 'rules[][parameters][dismiss_stale_reviews_on_push]=true' \
  -f 'rules[][type]=required_status_checks' \
  -F 'rules[][parameters][strict_required_status_checks_policy]=true' \
  -f 'rules[][parameters][required_status_checks][][context]=test' \
  -f 'rules[][parameters][required_status_checks][][context]=image'
```

Replace `test` / `image` with the repo's actual check names; one `context` line per check.
In a solo-maintainer repo set `required_approving_review_count=0` (and the same in Option B).
Drop the `image` line unless CI has an `image` job (§0).
`non_fast_forward` blocks force-pushes, `deletion` blocks branch deletion,
`strict_...=true` enforces up-to-date branches.

### Option B — classic branch protection via `gh`

```bash
gh api --method PUT repos/{owner}/{repo}/branches/main/protection \
  --input - <<'JSON'
{
  "required_pull_request_reviews": { "required_approving_review_count": 1,
                                     "dismiss_stale_reviews": true },
  "required_status_checks": { "strict": true, "contexts": ["test", "image"] },
  "enforce_admins": true,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

As in Option A, list only checks CI runs on PRs: `image` only when there is an `image` job.

`enforce_admins: true` matches §1's intent that the rule applies to everyone (the draft's
"Include administrators"). No `gh`? The same calls work through the GitHub REST API or the
`github` MCP tools; or configure it in the web UI: **Settings → Rules → Rulesets → New
branch ruleset** (or **Settings → Branches → Add rule**), targeting `main` with the same
options.

## 2. Verify

Read the protection back and confirm it's active — don't trust the POST alone:

```bash
gh api repos/{owner}/{repo}/rulesets                 # ruleset present, enforcement=active
gh api repos/{owner}/{repo}/branches/main --jq .protected   # → true
```

Open a throwaway PR (or check an open one): it should show the required check and block
merge until it's green and approved.

## Adding a check to existing protection (e.g. `image`)

When a repo gains a gating job, such as an `image` job on a first-party service, add its
context **after the job has reported on a PR** (so the name is exact and it can't hang PRs).
Don't recreate the protection; add to it. Find which mechanism holds the required checks:

```bash
repo=DavidWinterbottom-2/<repo>
gh api "repos/$repo/rules/branches/main" \
  --jq '.[] | select(.type=="required_status_checks") | .ruleset_id'   # ruleset id(s), if any
gh api "repos/$repo/branches/main/protection/required_status_checks" --jq .contexts  # classic, 404 if none
```

Ruleset: append the context to its `required_status_checks` rule and PUT it back.

```bash
id=<ruleset id from above>
gh api "repos/$repo/rulesets/$id" \
  | jq '{name, target, enforcement, conditions, bypass_actors,
         rules: [.rules[] | if .type == "required_status_checks"
                 then .parameters.required_status_checks |= (. + [{context: "image"}] | unique_by(.context))
                 else . end]}' \
  | gh api --method PUT "repos/$repo/rulesets/$id" --input -
```

Classic protection:

```bash
gh api --method POST "repos/$repo/branches/main/protection/required_status_checks/contexts" \
  -f 'contexts[]=image'
```

Then re-run §2's verify. Changing protection needs an admin-scoped token; a Claude session's
token gets 403, so hand the command to the repo owner.

## Enforcement

The account is on a paid GitHub plan, so rulesets and classic protection are **enforced** on
private repos as well as public ones. §1 is a hard requirement with no Free-plan deviation;
a `.hermes-ignore` line `§1 Branch protection` is no longer an accepted opt-out, so remove
one if you find it.

## Notes

- This is the human/Claude-run counterpart to Hermes's "`main` is not protected" finding
  against §1.
- Keep the required check list in sync with CI: if you rename or add a gating job, update the
  ruleset's `context` list or PRs will hang waiting on a check that never reports.
