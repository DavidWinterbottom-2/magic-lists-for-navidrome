---
name: promote-to-service
description: >-
  Turn an existing winterbottom.xyz repo (typically one bootstrapped from the
  copier template) into a running, self-hosted docker-infra service — the
  docker-compose deployment, tools-index + tools-page registration, README sync,
  Cloudflare DNS subdomain, and Entra SSO if it needs login. Use when a project is
  ready to go from "just a repo" to "a deployed service on the Pi / desktop / xps".
---

# Promote a repo to a docker-infra service

The copier template makes a standards-compliant **repo**; this skill makes it a
**service**. It sequences the existing infra skills rather than duplicating them.
Do the steps in order and keep the docker-infra CI gates green
(`check-tools-index`, `check-service-docs`).

## 0. Decide the shape

Ask / confirm:
- **Host** — `home-docker` (always-on Pi), `desktop-docker` (GPU), or `xps-docker` (dev).
- **Exposure** — `public` (internet via the Pi's Apache, gets a subdomain),
  `internal` (LAN/Tailscale only), or `none` (no web UI).
- **Login** — does it need Microsoft/Entra SSO (the `ALLOWED_EMAILS` pattern)?
- **Image** — does the repo publish a GHCR image (deployed service) or is it static?

## 1. Deployment files (in docker-infra)

Under `<host>/services/<name>/`, mirror an existing service (e.g. `ynab-ingestor`):
- `docker-compose.yml` — pull `ghcr.io/davidwinterbottom-2/<name>:latest`, join the
  external `docker-infra` network, expose a port, `restart: unless-stopped`, a
  healthcheck, and a bind-mounted data volume if needed.
- `Makefile` — set `SERVICE_NAME` / `SERVICE_PORT` (+ optional `SERVICE_DESC` and knobs)
  and `include ../service-common.mk` (first-party web app) or `mcp-common.mk` (MCP server)
  so it exposes the standard targets
  (`help install start stop restart logs status health clean pull config`). See §6.
- `README.md` + `.env.example`.

If the repo is a deployed image, ensure it has a `publish.yml` (GHCR build on push to
`main`) in its **own** repo, plus the §4 **`image`** PR check: build the image, run it, and
fail unless `/health` reports the declared version (the template's `ships_image` job, or
`asset-overview`'s). Once `image` has reported on a PR, add it to `main`'s required checks
(`configure-branch-protection`, "Adding a check").

## 2. Register it (this is what keeps CI green)

Follow the **`new-service-checklist`** skill — it walks the required edits:
- `scripts/tools-index.json` — add `"<host>/<name>": { "scope": "...", "match": "..." }`.
- The matching tools page (`public/tools/index.html` or `internal/index.html`) — add a
  card; the `match` string (usually the URL) must appear on the page.
- `<host>/services/README.md` — add the service row/port so `check-service-docs` passes.
- Design system (§8): if it has a web UI, use the shared `winterbottom.css` /
  `winterbottom-theme.js` (see `design-system-sync`).

## 3. DNS (public services)

Use the **`cloudflare-dns`** skill to add the subdomain (e.g. `<name>.winterbottom.xyz`
→ CNAME to the zone apex, DNS-only, TTL 600), then add the Apache vhost on the Pi.

## 4. SSO (if it needs login)

Use the **`entra-app-registration`** skill to create/update the Entra app, set the
redirect URI, mint a client secret, and emit the
`AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / ALLOWED_EMAILS` values for
the service's `.env`.

## 5. Deploy + verify

- On the host: `make install` (or `make pull && make start`).
- `docker compose -f <host>/services/<name>/docker-compose.yml config` validates.
- `make health` / hit `/health`.
- Add a dated entry to the service README **Updates** section (§3).
- Run `python scripts/check-tools-index.py` and `python scripts/check-service-docs.py` —
  both must pass before the PR merges.

## 6. Hand back to Hermes

Add `DavidWinterbottom-2/<name>` to Hermes's `TARGET_REPOS` if it isn't already, so the
new service is audited against REPO-STANDARDS each cycle.
