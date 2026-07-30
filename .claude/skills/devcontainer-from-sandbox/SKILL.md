---
name: devcontainer-from-sandbox
description: >-
  Add a VS Code devcontainer to a repo by templating off the devcontainer-sandbox
  repo, so the code runs in a reproducible container (VS Code "Reopen in
  Container" / Codespaces). Use when a repo has no .devcontainer (REPO-STANDARDS
  §7), or when standing up a new project's dev environment.
---

# Devcontainer from the sandbox template

REPO-STANDARDS §7 requires every project to be templated off
[`devcontainer-sandbox`](https://github.com/DavidWinterbottom-2/devcontainer-sandbox) so it
runs in a **VS Code devcontainer / Codespaces** — a one-command, reproducible dev
environment instead of per-machine setup.

## Adopt it

1. Clone the template and copy its **lean** `post-create.sh` — the one the copier template
   ships under `template/.devcontainer/`, **not** the sandbox's own heavy multi-language
   `.devcontainer/`:
   ```bash
   git clone --depth 1 https://github.com/DavidWinterbottom-2/devcontainer-sandbox /tmp/dcs
   mkdir -p .devcontainer
   cp /tmp/dcs/template/.devcontainer/post-create.sh .devcontainer/post-create.sh
   ```
   `post-create.sh` is the shared, stack-agnostic conveniences layer: it installs deps
   (from `pyproject.toml` / `requirements.txt` / `package.json`) **and clones + installs the
   user's dotfiles**. Bring it over as-is — don't hand-roll a devcontainer that drops it
   (that's how the dotfiles go missing).
2. **Write `.devcontainer/devcontainer.json`** tailored to the repo's stack — base image or
   `features` (Python / Node / Rust …), any `forwardPorts` the app uses, editor extensions —
   and set `"postCreateCommand": "bash .devcontainer/post-create.sh"`. Use
   `template/.devcontainer/devcontainer.json.jinja` as the shape.
3. **Add any stack-specific *system* deps to `post-create.sh`** (e.g. `sudo apt-get install
   -y ffmpeg`) rather than inlining them in `postCreateCommand` — that keeps deps and the
   dotfiles step together, and nothing gets dropped.
4. Commit on a feature branch and verify: in VS Code, **Reopen in Container** (or open the
   repo in a Codespace) and confirm the toolchain, tests, **and your dotfiles** are present
   inside the container.

## Notes

- **Checkable:** the repo now has `.devcontainer/devcontainer.json` (Hermes's `has_devcontainer`
  signal / `No devcontainer` finding).
- Keep the devcontainer aligned with the sandbox as it evolves; where practical, reference the
  template rather than diverging.
- A repo that genuinely shouldn't have one can silence the finding with a `.hermes-ignore`
  line `§7 Dev environment`.
