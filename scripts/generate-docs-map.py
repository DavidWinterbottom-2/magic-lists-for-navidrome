#!/usr/bin/env python3
"""Render the README's documentation map from AGENTS.md.

AGENTS.md is the single source of truth for which files direct an agent and what
each is for: its "Where the detail lives" table. The README's map is a rendered
view of that table, and the "Points to" column is derived from the files
themselves — so neither column can drift from reality by hand.

    python scripts/generate-docs-map.py            # rewrite the README block
    python scripts/generate-docs-map.py --check     # exit 1 if it's stale (CI)

To change the map, edit the table in AGENTS.md and re-run this. Never edit the
generated block in README.md.

`--check` enforces two different things, and both are needed:

  consistency  — the README's table matches AGENTS.md and the files on disk.
  completeness — no markdown exists that the table doesn't account for. Without
                 this, a new doc could be added and the gate stays green while
                 the map silently omits it, which is the exact failure the map
                 is meant to prevent. It caught e2e/README.md the day it was
                 added.

Possible candidate for the devcontainer-sandbox template, as a shared skill
rather than a vendored file — the map's *content* is per-repo, so only the
generator travels. Not proposed yet: it wants a few weeks of use here first, and
the completeness gap above is the sort of thing that only shows up with time.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"
README = ROOT / "README.md"

CANONICAL_MARKER = "<!-- docs-map:canonical -->"
BEGIN = "<!-- BEGIN GENERATED docs-map — edit AGENTS.md, then run scripts/generate-docs-map.py -->"
END = "<!-- END GENERATED docs-map -->"

# The root of the tree is the anchor, not one of its own destinations, so it
# isn't a row in AGENTS.md's table and its description lives here instead. This
# is the only hand-written text in the generated table.
ROOT_DOC = "AGENTS.md"
ROOT_SUMMARY = "The entry point. Project description, the exact commands, the rules that apply to every change, and pointers to everything below. Kept deliberately short — it is re-read on every request."

# Markdown that is deliberately not an entry in the map. Everything else must be
# listed, or --check fails: consistency between AGENTS.md and the README says
# nothing about whether a NEW doc was added and never mentioned, and a map that
# silently omits a file is the failure the map exists to prevent.
EXEMPT_FILES = {
    "AGENTS.md",        # the root itself
    "README.md",        # human-facing
    "SETUP.md",         # human-facing
    "OLLAMA_SETUP.md",  # human-facing
    "CLAUDE.md",        # listed as an entry, and its own root
}
# Directories covered by a single entry, or owned elsewhere entirely. Hidden
# directories are skipped wholesale (see scan below), which covers .claude/,
# .github/ and the various tool caches that ship their own README.
EXEMPT_DIRS = ("standards/", "node_modules/", "venv/", "htmlcov/", "test-results/")

TABLE_ROW = re.compile(r"^\|(?P<purpose>[^|]+)\|(?P<target>[^|]+)\|\s*$")
LINK = re.compile(r"\]\((?P<path>[^)#]+)(?:#[^)]*)?\)")
CODE_PATH = re.compile(r"`(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z0-9]+|[A-Za-z0-9_.-]+/)`")


def parse_canonical():
    """Read AGENTS.md's table into [(path, purpose)], in declared order."""
    lines = AGENTS.read_text().splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if CANONICAL_MARKER in l)
    except StopIteration:
        sys.exit(f"error: {CANONICAL_MARKER} not found in AGENTS.md")

    entries = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        m = TABLE_ROW.match(line)
        if not m:
            continue
        purpose, target = m.group("purpose").strip(), m.group("target").strip()
        if purpose in ("For", "---") or set(purpose) <= {"-", " "}:
            continue  # header / separator
        link = LINK.search(target)
        if not link:
            continue
        entries.append((link.group("path"), purpose, target))
    if not entries:
        sys.exit("error: no rows parsed from AGENTS.md's docs-map table")
    return entries


def markdown_files(path: Path):
    """The files whose links represent this entry (a dir contributes its docs)."""
    if path.is_dir():
        return sorted(path.rglob("*.md"))
    return [path] if path.is_file() else []


def references_of(entry_path: str, canonical: list):
    """Which OTHER entries in the map this entry points at.

    Deliberately not "every file this doc mentions": that lists `requirements.txt`
    and a dozen backend modules, which says nothing about how the documents
    relate. Narrowing it to the canonical set makes the column a graph of the doc
    structure — which is what answers "does this point in circles?".

    Both markdown links and backticked paths count, since some docs link their
    references and others name them in prose.
    """
    target = ROOT / entry_path
    own = normalise(entry_path)
    found = set()

    for f in markdown_files(target):
        text = f.read_text()
        for m in list(LINK.finditer(text)) + list(CODE_PATH.finditer(text)):
            raw = m.group("path").strip()
            if raw.startswith(("http://", "https://", "mailto:")):
                continue
            # Links are relative to the containing file; backticked paths are
            # written relative to the repo root. Try both.
            for c in (ROOT / raw, f.parent / raw):
                if not c.exists():
                    continue
                try:
                    rel = str(c.resolve().relative_to(ROOT))
                except ValueError:
                    break
                hit = owning_entry(rel, canonical)
                if hit and hit != own:
                    found.add(hit)
                break

    return sorted(found)


def normalise(path: str) -> str:
    return path.rstrip("/")


def owning_entry(path: str, canonical: list):
    """The canonical entry a path belongs to, if any.

    A reference to `.claude/skills/create-pr/SKILL.md` counts as a reference to
    the `.claude/skills/` entry.
    """
    for entry in canonical:
        e = normalise(entry)
        if path == e or path.startswith(e + "/"):
            return e
    return None


def render():
    rows = [(ROOT_DOC, ROOT_SUMMARY, f"[`{ROOT_DOC}`]({ROOT_DOC})")]
    rows += list(parse_canonical())
    canonical = [path for path, _, _ in rows]

    out = ["| Document | What it directs | Points to |", "| --- | --- | --- |"]
    for path, purpose, target in rows:
        refs = references_of(path, canonical)
        cell = ", ".join(f"`{r}`" for r in refs) if refs else "— (leaf)"
        out.append(f"| {target} | {purpose} | {cell} |")
    return "\n".join(out)


def splice(text, block):
    if BEGIN not in text or END not in text:
        sys.exit(f"error: generated-block markers not found in {README.name}")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n{block}\n{END}{tail}"


def unlisted_docs(canonical):
    """Markdown in the repo that no entry in the map accounts for."""
    listed = [normalise(p) for p in canonical]
    missing = []
    for path in sorted(ROOT.rglob("*.md")):
        rel = path.relative_to(ROOT)
        if any(part.startswith(".") for part in rel.parts):
            continue  # .claude/, .github/, and tool caches with their own README
        rel = str(rel)
        if rel in EXEMPT_FILES or rel.startswith(EXEMPT_DIRS):
            continue
        if not owning_entry(rel, listed):
            missing.append(rel)
    return missing


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if README.md is out of date instead of rewriting it")
    args = ap.parse_args()

    current = README.read_text()
    updated = splice(current, render())

    canonical = [ROOT_DOC] + [path for path, _, _ in parse_canonical()]
    missing = unlisted_docs(canonical)
    if missing:
        print("Documentation not accounted for in AGENTS.md's table:", file=sys.stderr)
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        print("Add it to the table, or to EXEMPT_FILES/EXEMPT_DIRS if it is not "
              "meant to direct an agent.", file=sys.stderr)
        return 1

    if args.check:
        if current != updated:
            print("README.md's documentation map is stale.", file=sys.stderr)
            print("Edit the table in AGENTS.md, then run: "
                  "python scripts/generate-docs-map.py", file=sys.stderr)
            return 1
        print("documentation map is up to date")
        return 0

    if current == updated:
        print("documentation map already up to date")
        return 0
    README.write_text(updated)
    print(f"rewrote the documentation map in {README.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
