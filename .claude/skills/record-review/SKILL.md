---
name: record-review
description: >-
  Append a dated entry to a repo's review log (docs/reviews/LOG.md) after a code
  or architecture review has run, in the machine-readable format Hermes reads for
  §9 Review cadence. Use right after finishing the code-review or
  improve-codebase-architecture skill, so the review counts as evidence.
---

# Record a review in the log

REPO-STANDARDS §9 tracks that reviews happen on cadence, using a **committed review log** as
the evidence. Reviews themselves are run by the `code-review` /
`improve-codebase-architecture` skills; this skill just records that one ran, in the exact
format Hermes parses. Run it **immediately after** a review finishes.

## The log

`docs/reviews/LOG.md`, newest entry first, one line per review:

```
- YYYY-MM-DD | <type> | <scope> | <note or PR link>
```

- `<type>` — `code-review` or `architecture-review` (exactly these; Hermes keys off them).
- `<scope>` — what was reviewed: a PR (`PR #42`), a module, `full repo`, a ref range.
- `<note>` — one short line: headline finding, or "no issues".

## Steps

1. Create the log if it's missing:

   ```bash
   mkdir -p docs/reviews
   [ -f docs/reviews/LOG.md ] || printf '# Review log\n\nReviews run on this repo (REPO-STANDARDS §9), newest first.\nEach line: `- YYYY-MM-DD | <type> | <scope> | <note>`.\n\n' > docs/reviews/LOG.md
   ```

2. Insert today's entry **above the existing entries** (newest first). Use the real date:

   ```bash
   today=$(date +%F)
   # edit docs/reviews/LOG.md and add, e.g.:
   # - 2026-07-24 | code-review | PR #42 | standards + spec, 2 findings fixed
   ```

3. Commit it with the change it reviewed (or on its own commit if the review was standalone):

   ```bash
   git add docs/reviews/LOG.md && git commit -m "docs: record <type> review"
   ```

## Notes

- One entry per review actually performed — don't back-fill dates. Hermes reads the **newest**
  date per type, so an honest log keeps the cadence check meaningful.
- Repos with no application code (pure config/infra) don't need reviews; they opt out with a
  `.hermes-ignore` line `§9 Review cadence` instead of keeping an empty log.
