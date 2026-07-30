#!/bin/bash
# SessionStart hook — installs Python dependencies so tests and linters work in
# Claude Code on the web sessions. Runs synchronously (deps are guaranteed ready
# before the session starts). Safe to run repeatedly (idempotent).
set -euo pipefail

# Only run in Claude Code on the web (remote) sessions; local runs are a no-op.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

echo "[session-start] Installing Python dependencies from requirements.txt..." >&2
pip install --quiet --disable-pip-version-check -r requirements.txt

# Make the backend package importable from the repo root for tests/tools.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PYTHONPATH=\"${CLAUDE_PROJECT_DIR:-.}\"" >> "$CLAUDE_ENV_FILE"
fi

echo "[session-start] Dependencies installed." >&2
