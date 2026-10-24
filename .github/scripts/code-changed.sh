#!/usr/bin/env bash
# Prints code=true|false for the files changed between two commits (default:
# HEAD^1..HEAD — on a pull_request checkout HEAD is the merge commit and HEAD^1
# the base tip, so that is exactly the PR's diff). code=false means every
# changed file is documentation, which ships in neither the image nor the tests,
# so CI can skip its heavy jobs and version-check can skip the bump.
# Keep DOCS_ONLY in step with "Every change" in AGENTS.md (REPO-STANDARDS §2).
set -euo pipefail

base="${1:-HEAD^1}"
head="${2:-HEAD}"

code=false
while IFS= read -r path; do
  [ -z "$path" ] && continue
  case "$path" in
    .github/*) code=true ;;  # workflow changes must exercise CI
    # The Dockerfile copies exactly these folders into the image, so a doc
    # inside one of them ships and counts as code.
    backend/* | frontend/* | recipes/*) code=true ;;
    # Docs: nothing the Dockerfile copies, and nothing tests/ or e2e/ read.
    # (The *.md files are read by scripts/generate-docs-map.py --check, but that
    # runs in the ungated lint job, so a docs-only PR still checks the map.)
    *.md | docs/* | openspec/* | .claude/* | LICENSE) ;;
    *) code=true ;;
  esac
  [ "$code" = true ] && break
done < <(git diff --name-only "$base" "$head")

echo "code=$code"
