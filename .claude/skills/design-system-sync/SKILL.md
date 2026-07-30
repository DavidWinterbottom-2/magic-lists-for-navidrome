---
name: design-system-sync
description: >-
  Adopt or refresh the shared winterbottom design system in a web-UI repo —
  vendor winterbottom.css + winterbottom-theme.js from docker-infra/design-system
  and style through its tokens. Use when a repo serves HTML but doesn't use the
  shared look (REPO-STANDARDS §8), or when the design system has moved on and a
  repo's vendored copies have drifted.
---

# Design-system sync

REPO-STANDARDS §8 requires every winterbottom.xyz **web UI** to share one look — the
**winterbottom design system**. The canonical assets and the doc live in
[`docker-infra/design-system/`](https://github.com/DavidWinterbottom-2/docker-infra/tree/main/design-system):

- `winterbottom.css` — the token-driven stylesheet (colours, spacing, type, components)
- `winterbottom-theme.js` — the light/dark theme toggle + token wiring
- `LOOK-AND-FEEL.md` — the design doc (how to use the tokens; "Keeping apps in sync")
- `style-guide.html` — a live style guide you can open to see every token/component

Apps **vendor** the two shared files and style through the tokens rather than hand-rolling
colours, spacing or type. `docker-infra` owns the source and is exempt.

## Adopt it (repo has no shared styling yet)

1. Copy the two canonical files into the repo (keep the names so Hermes/`§8` detect them):

   ```bash
   # from the repo root; adjust the local docker-infra path
   src=../docker-infra/design-system
   dest=.            # or web/, static/, public/, assets/ … wherever the app serves from
   cp "$src/winterbottom.css" "$src/winterbottom-theme.js" "$dest/"
   ```

2. Reference them from every page's `<head>`:

   ```html
   <link rel="stylesheet" href="winterbottom.css">
   <script src="winterbottom-theme.js" defer></script>
   ```

3. Replace bespoke colours/spacing/type with the design **tokens** (CSS custom properties)
   from `winterbottom.css`. Read `LOOK-AND-FEEL.md` and open `style-guide.html` for the token
   names and the component patterns. Don't fork the tokens — if something's missing, propose
   it *upstream* in `docker-infra/design-system`.

## Keep it in sync (repo already vendors it)

The vendored copies are exactly that — copies — so they drift as the system evolves. To
refresh:

```bash
src=../docker-infra/design-system
diff -u winterbottom.css "$src/winterbottom.css"                # see what changed
cp "$src/winterbottom.css" "$src/winterbottom-theme.js" .       # take the latest
```

Commit the refresh on its own branch/PR so the visual change is reviewable. If the app has
intentionally customised a token, prefer overriding it in a small app-local stylesheet loaded
*after* `winterbottom.css` — never by editing the vendored file, or the next sync clobbers it.

## Check

- Every served HTML page links `winterbottom.css` and `winterbottom-theme.js`.
- No bespoke colour/spacing/type values that duplicate an existing token.
- The vendored files match `docker-infra/design-system` (or differences are deliberate and
  documented).
