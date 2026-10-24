# Review stage: which reviews, and how to run them

Pick the set by the issue's size, decided at define:

| Review | Small | Big | How |
|---|---|---|---|
| Architecture | — | ✓ | `architecture-critic` agent |
| Code (standards + spec) | ✓ | ✓ | the `code-review` skill, run in a sub-agent |
| Security | ✓ | ✓ | `security-critic` agent |
| Documentation | ✓ | ✓ | a general-purpose agent with the brief below |

Run them **in parallel and in the background**. Brief each one like a peer:
- the issue, the branch and the diff command (`git diff origin/main...HEAD`, since cloud clones have only `origin/main`; plus any cross-repo branch);
- the spec paths;
- what to skip (vendored files such as `.claude/skills/openspec-*`);
- facts the sandbox can't check (for example, a host it can't reach).

Give them the code, not your conclusions. Ask for findings ranked blocking / should-fix / optional, each with `file:line` and the cheapest fix, ending `VERDICT: PASS` or `ISSUES FOUND`.

**Documentation brief:** check that a reader can rely on the docs after this change.
- `CLAUDE.md`, `README.md`, `ARCHITECTURE.md` (architecture decisions belong there), `CHANGELOG.md`, and comments and docstrings in the changed code.
- The OpenSpec artifacts must agree with each other and with the final code. Decisions change between stages, which leaves stale text behind.
- Contributor docs for any new workflow.

## Iterating

1. Classify every finding: **ACCEPT** (fix it), **REJECT** (say why: already covered, out of scope, or not worth its code), or **DEFER** (name where it will be done, for example another issue).
2. Verify a fix before trusting it. In #48, a reviewer's suggested `git log -S` wouldn't have worked, because the version line's count doesn't change on a bump; `-G` does. A merge-commit fix was proven on a scratch repo with a real merge commit.
3. Fix everything you accepted in **one batch** once all reviews have reported, so files aren't edited twice. Run tests, push, and wait for CI.
4. Send each review that said `ISSUES FOUND` back, continuing the same agent, with the fix commit, `git diff <old>..<new>`, and your classification of its findings. Ask it to re-review only the change and anything it affects.
5. Repeat until each one says PASS.

## Logging

Use the `record-review` skill. The log's types are only `code-review` and `architecture-review`, so record security, documentation and test reviews inside the `code-review` entry's note. Add one entry per review that ran, and never back-fill.
