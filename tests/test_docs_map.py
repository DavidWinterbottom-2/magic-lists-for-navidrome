"""Tests for scripts/generate-docs-map.py's handling of vendored docs.

The README's map is checked in CI, so anything that changes it without a repo
edit breaks lint on a PR that didn't touch the docs. Vendored entries are
rewritten upstream by template-sync, so their contents must not feed the map;
the repo's own docs still must.

Run from the repo root:
    python -m unittest tests.test_docs_map
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "generate-docs-map.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_docs_map", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AGENTS = """# Agents

<!-- docs-map:canonical -->

| For | Read |
| --- | --- |
| Style | [`docs/style.md`](docs/style.md) |
| Task procedures | [`.claude/skills/`](.claude/skills/) |
"""


class VendoredEntriesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "AGENTS.md").write_text(AGENTS)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "style.md").write_text("No links yet.\n")
        self.skill = self.root / ".claude" / "skills" / "tdd" / "SKILL.md"
        self.skill.parent.mkdir(parents=True)
        self.skill.write_text("See [style](../../../docs/style.md).\n")

        self.gen = load_generator()
        self.gen.ROOT = self.root
        self.gen.AGENTS = self.root / "AGENTS.md"

    def tearDown(self):
        self.tmp.cleanup()

    def test_vendored_entry_is_not_scanned(self):
        row = next(l for l in self.gen.render().splitlines() if ".claude/skills/" in l)
        self.assertTrue(row.endswith(f"| {self.gen.VENDORED_CELL} |"), row)

    def test_upstream_edit_to_a_vendored_doc_leaves_the_map_unchanged(self):
        before = self.gen.render()
        self.skill.write_text("See [agents](../../../AGENTS.md) and `docs/style.md`.\n")
        self.assertEqual(before, self.gen.render())

    def test_edit_to_a_repo_owned_doc_still_changes_the_map(self):
        before = self.gen.render()
        (self.root / "docs" / "style.md").write_text("Start at [`AGENTS.md`](../AGENTS.md).\n")
        after = self.gen.render()
        self.assertNotEqual(before, after)
        self.assertIn("| `AGENTS.md` |", after)


if __name__ == "__main__":
    unittest.main()
