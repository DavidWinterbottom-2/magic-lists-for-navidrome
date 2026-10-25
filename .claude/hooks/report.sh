#!/bin/sh
# Claude Code hook: report this event to the claude-tracker, in the background.
#
# Copied into each dev repo as .claude/hooks/report.sh next to report.py and
# registered in .claude/settings.json (see hooks/README.md in claude-tracker).
# It always exits 0 at once, so it can never block or fail a session; the
# post itself gives up after 3 seconds. Outside the custom cloud environment
# CLAUDE_EVENTS_URL or CLAUDE_EVENTS_TOKEN is unset and it does nothing. A repo
# opts out by committing an empty .claude/tracker-off next to this hooks folder.
[ -n "${CLAUDE_EVENTS_URL:-}" ] && [ -n "${CLAUDE_EVENTS_TOKEN:-}" ] || exit 0
[ -e "$(dirname "$0")/../tracker-off" ] && exit 0
command -v python3 >/dev/null 2>&1 || exit 0
here=$(dirname "$0")
payload=$(mktemp 2>/dev/null) || exit 0
cat >"$payload"
CLAUDE_EVENT_TS=$(date +%s.%N 2>/dev/null || date +%s)
export CLAUDE_EVENT_TS
(
  timeout 10 python3 "$here/report.py" <"$payload"
  rm -f "$payload"
) >/dev/null 2>&1 </dev/null &
exit 0
