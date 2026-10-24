#!/usr/bin/env bash
# Prints code=true|false for the files changed between two commits (default:
# HEAD^1..HEAD — on a pull_request checkout HEAD is the merge commit and HEAD^1
# the base tip, so that is exactly the PR's diff). code=false means every
# changed file is documentation, which ships in neither the image nor the tests,
# so CI can skip its heavy jobs and version-check can skip the bump.
# Keep the docs list in step with "CI and versioning" in CLAUDE.md.
#
# Allowlist docs; everything else is code, so a missing rule errs towards
# running CI and requiring a bump (REPO-STANDARDS §2).
set -euo pipefail

base="${1:-HEAD^1}"
head="${2:-HEAD}"

code=false
while IFS= read -r path; do
  [ -z "$path" ] && continue
  case "$path" in
    .github/*) code=true ;;  # workflow changes must exercise CI
    # The Dockerfile COPYs exactly backend/, frontend/ and recipes/ (plus
    # requirements.txt), so anything under them ships, a .md included.
    backend/* | frontend/* | recipes/*) code=true ;;
    # Docs: no Dockerfile COPY reaches them and no test or app code reads them.
    # The one reader of *.md is the lint job's docs-map check, which is never
    # skipped. e2e/README.md and the rest fall out of the image the same way.
    *.md | docs/* | openspec/* | .claude/* | LICENSE) ;;
    *) code=true ;;
  esac
  [ "$code" = true ] && break
done < <(git diff --name-only "$base" "$head")

echo "code=$code"
