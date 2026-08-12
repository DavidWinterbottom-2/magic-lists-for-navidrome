#!/usr/bin/env bash
#
# Sync this repo's shared assets from the devcontainer-sandbox template, so the
# vendored copies don't silently drift from the canonical library. Two kinds of
# asset are pulled, both declared in .github/template-sync.json:
#
#   - shared Claude Code skills  → .claude/skills/<name>   ("shared_skills")
#   - shared docs                → any src→dest path pair  ("shared_docs")
#
# The repo's own skills ("own_skills") are left untouched.
#
# Run by .github/workflows/template-sync.yml on a schedule / manual dispatch,
# which opens a PR when anything changes. Also runnable locally:
#
#     GH_TOKEN=<pat> scripts/sync-from-template.sh
#
# The template repo is private, so reading it needs a token with Contents:read
# on DavidWinterbottom-2/devcontainer-sandbox. In CI that is the
# TEMPLATE_SYNC_TOKEN secret; the workflow's own GITHUB_TOKEN cannot read another
# private repo. Pass it via $GH_TOKEN — the script wires it into the clone.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
manifest="${repo_root}/.github/template-sync.json"

command -v jq >/dev/null 2>&1 || { echo "error: jq is required" >&2; exit 1; }
[ -f "$manifest" ] || { echo "error: manifest not found: $manifest" >&2; exit 1; }

source_repo="$(jq -r '.source.repo' "$manifest")"
source_ref="$(jq -r '.source.ref'  "$manifest")"
skills_src_path="$(jq -r '.skills.path' "$manifest")"
skills_dest="${repo_root}/$(jq -r '.skills.dest' "$manifest")"
mapfile -t shared_skills < <(jq -r '.shared_skills[]' "$manifest")
doc_count="$(jq -r '.shared_docs | length' "$manifest")"

if [ "${#shared_skills[@]}" -eq 0 ] && [ "$doc_count" -eq 0 ]; then
  echo "Nothing declared to sync in ${manifest}."
  exit 0
fi

: "${GH_TOKEN:?error: set GH_TOKEN to a token that can read ${source_repo} (Contents:read)}"

echo "Syncing from ${source_repo}@${source_ref}:"
echo "  skills: ${shared_skills[*]:-(none)}"
echo "  docs:   ${doc_count}"

workdir="$(mktemp -d)"
cleanup() { rm -rf "$workdir"; }
trap cleanup EXIT

# Shallow, blobless checkout of the whole template tree — we need both the
# skills dir and arbitrary standards paths, so a broad sparse set is simplest.
clone_url="https://x-access-token:${GH_TOKEN}@github.com/${source_repo}.git"
git clone --depth 1 --branch "$source_ref" --filter=blob:none \
  "$clone_url" "$workdir/template" >/dev/null 2>&1 || {
    echo "error: could not clone ${source_repo}@${source_ref}." >&2
    echo "       Check the branch exists and GH_TOKEN has Contents:read on it." >&2
    exit 1
  }

missing=0

# ── Skills ────────────────────────────────────────────────────────────────────
if [ "${#shared_skills[@]}" -gt 0 ]; then
  mkdir -p "$skills_dest"
  for skill in "${shared_skills[@]}"; do
    src="$workdir/template/${skills_src_path}/${skill}"
    if [ ! -d "$src" ]; then
      echo "warning: skill '${skill}' not found in ${source_repo}:${skills_src_path} — skipping" >&2
      missing=1
      continue
    fi
    # Replace wholesale so deletions in the template propagate too.
    rm -rf "${skills_dest:?}/${skill}"
    cp -a "$src" "${skills_dest}/${skill}"
    echo "  synced skill  ${skill}"
  done
fi

# ── Standards docs ────────────────────────────────────────────────────────────
if [ "$doc_count" -gt 0 ]; then
  while IFS=$'\t' read -r doc_src doc_dest; do
    src="$workdir/template/${doc_src}"
    dest="${repo_root}/${doc_dest}"
    if [ ! -f "$src" ]; then
      echo "warning: doc '${doc_src}' not found in ${source_repo} — skipping" >&2
      missing=1
      continue
    fi
    mkdir -p "$(dirname "$dest")"
    cp -a "$src" "$dest"
    echo "  synced doc    ${doc_dest}"
  done < <(jq -r '.shared_docs[] | [.src, .dest] | @tsv' "$manifest")
fi

if [ "$missing" -ne 0 ]; then
  echo "note: one or more assets were missing upstream (see warnings above)." >&2
fi

echo "Done."
