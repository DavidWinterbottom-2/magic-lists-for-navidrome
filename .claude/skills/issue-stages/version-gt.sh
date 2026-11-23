#!/usr/bin/env bash
# version-gt.sh A B — is version A strictly greater than version B (semver)?
#
# Exits 0 if A > B, 1 if A <= B, and 2 (with a message naming the value on
# stderr) if either isn't plain MAJOR.MINOR.PATCH: digits only, no leading
# zeros, no `v` prefix, no pre-release or build suffix. Fields compare as
# numbers, so 1.10.0 > 1.9.0. No dependencies: `sort -V` isn't semver.
#
# Used by CI's version-bumped job (a code PR's version must be greater than
# the base branch's) and by the issue-stages deploy gate (a candidate must be
# greater than the live version). Tested by devcontainer-sandbox's
# scripts/test-version-gt.sh; edit it there, not in a downstream copy. Two
# copies, kept identical by that test: template/.github/scripts/ (CI) and
# .claude/skills/issue-stages/ (the deploy gate, synced by template-sync).
set -euo pipefail
export LC_ALL=C   # [[ < ]] collates by locale; C makes it byte order

if [ "$#" -ne 2 ]; then
  echo "usage: version-gt.sh A B   (exit 0 if A > B, 1 if A <= B)" >&2
  exit 2
fi

field='(0|[1-9][0-9]*)'
for v in "$1" "$2"; do
  if ! [[ "$v" =~ ^$field\.$field\.$field$ ]]; then
    # Shown with anything outside [0-9A-Za-z.+-] as '?': in CI the value comes
    # from a PR's own file, and a newline in it could emit a workflow command.
    echo "version-gt.sh: '${v//[^0-9A-Za-z.+-]/?}' is not a plain MAJOR.MINOR.PATCH version" >&2
    exit 2
  fi
done

IFS=. read -r -a a <<<"$1"
IFS=. read -r -a b <<<"$2"
for i in 0 1 2; do
  x="${a[i]}" y="${b[i]}"
  # No leading zeros, so a longer field is a bigger number, and fields of the
  # same length compare digit by digit. Never overflows, unlike $(( )).
  if ((${#x} != ${#y})); then
    if ((${#x} > ${#y})); then exit 0; else exit 1; fi
  fi
  if [[ "$x" > "$y" ]]; then exit 0; fi
  if [[ "$x" < "$y" ]]; then exit 1; fi
done
exit 1
