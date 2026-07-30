#!/usr/bin/env bash
# post-create.sh — first-run setup for the devcontainer.
#
# Two jobs, both stack-agnostic:
#   1. install this project's dependencies (detected from the files present)
#   2. clone + install your personal dotfiles
#
# Deliberately lean. Add project-specific steps at the bottom as needed.
set -euo pipefail

echo "🔧 post-create: installing project dependencies…"
if [ -f pyproject.toml ]; then
  pip install -e ".[dev]" || pip install -e .
elif [ -f requirements.txt ]; then
  pip install -r requirements.txt
  [ -f requirements-dev.txt ] && pip install -r requirements-dev.txt
fi
if [ -f package.json ]; then
  npm install
fi

# ── Personal dotfiles ────────────────────────────────────────────────────────
# Cloned into the container so your shell/editor config comes with you. A clone
# or install failure is non-fatal — the container still comes up.
DOTFILES_REPO="https://github.com/DavidWinterbottom-2/dotfiles"
DOTFILES_DIR="$HOME/.dotfiles"
if [ ! -d "$DOTFILES_DIR" ]; then
  echo "🔧 post-create: cloning dotfiles…"
  git clone --depth=1 "$DOTFILES_REPO" "$DOTFILES_DIR" || echo "⚠️  dotfiles clone failed (continuing)"
fi
if [ -f "$DOTFILES_DIR/install.sh" ]; then
  chmod +x "$DOTFILES_DIR/install.sh"
  "$DOTFILES_DIR/install.sh" || echo "⚠️  dotfiles install.sh failed (continuing)"
fi

echo "✅ post-create complete."
