---
name: new-service-checklist
description: >-
  Add (or remove) a self-hosted service in docker-infra without breaking CI.
  Use whenever creating a new home-docker/desktop-docker/xps-docker service, or
  retiring one — it walks the tools-index registration, tools-page card, README
  sync and design-system rules the check-tools-index and check-service-docs CI
  gates enforce.
---

# New service checklist (docker-infra)

A service is not "done" until the tools pages and READMEs agree it exists. Two CI checks
(`scripts/check-tools-index.py`, `scripts/check-service-docs.py`) fail until every step
below is satisfied — by design, so a service can't be added without a conscious decision
about whether it appears on a tools page.

## Adding a service

1. **Create `<tree>/services/<name>/`** with, at minimum: `docker-compose.yml`, `README.md`,
   `Makefile`, and `.env.example` (never commit `.env`). Add a healthcheck to the compose.
   **Image tag** (§2/§6): a *first-party* service pulls
   `ghcr.io/davidwinterbottom-2/<name>:latest` (Watchtower-updated); a *third-party* service
   pins the upstream tag — `image: foo:${FOO_VERSION:-<version>}` or a hard pin — not a bare
   `:latest`.
   Don't hand-roll the `Makefile` — set the service variables and `include` the shared
   template (both in `home-docker/services/`) so it exposes the standard targets
   (`help install start stop restart logs status health clean pull config`):
   - first-party web app (own ghcr.io image) → `service-common.mk`:
     ```make
     SERVICE_NAME := <name>
     SERVICE_PORT := <port>
     SERVICE_DESC := <one-line description>
     include ../service-common.mk
     ```
     Optional knobs: `SERVICE_DATA_DIRS`, `SERVICE_REQUIRE_ENV`, `SERVICE_CLEAN_VOLUMES`,
     `SERVICE_CLEAN_IMAGES`, `SERVICE_EXTRA_HELP`, or `CUSTOM_INSTALL := 1` (+ your own
     `install:`) for a bespoke install.
   - MCP server → `mcp-common.mk` (set `SERVICE_NAME` / `SERVICE_PORT`).
   - Third-party image with bespoke ops → a standalone `Makefile` is fine, but expose the
     same target names.

2. **Classify it in [`scripts/tools-index.json`](../../../scripts/tools-index.json)**, keyed
   `"<tree>/<service>"`, as exactly one of:
   - `public` — reachable from the internet via the Pi's Apache. Add a card to the public
     tools page and set `match` to a string on that page (usually its URL).
   - `internal` — LAN/Tailscale-only. Add a card to the internal page under its host, linked
     by MagicDNS name (`pi-home` / `desktop-home` / `xps-home`); `match` is e.g.
     `pi-home:8012`.
   - `none` — no web UI (databases, exporters, the proxy).

3. **Add the card to the matching tools page** so the `match` string is actually present:
   - public: `home-docker/services/apache/sites/winterbottom.xyz/public/tools/index.html`
   - internal: `.../winterbottom.xyz/internal/index.html`
   Style via the shared design-system tokens (`design-system/winterbottom.css`); copy an
   existing card as the template.

4. **Sync the tree README** (`<tree>/README.md`): add the service to the structure tree and
   the "Available Services" list. If the README documents ports in a table, add the row with
   the published host port from the compose file.

5. **If it needs a subdomain or SSO**, use the `cloudflare-dns` and `entra-app-registration`
   skills.

6. **Run the gates locally** before pushing:
   ```bash
   python3 scripts/check-tools-index.py
   python3 scripts/check-service-docs.py
   docker compose -f <tree>/services/<name>/docker-compose.yml config
   ```

## Removing a service

Delete the service directory, drop its `tools-index.json` entry, remove its card from the
tools page, and remove it from the tree README. Re-run both checks.
