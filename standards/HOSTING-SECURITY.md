# HOSTING-SECURITY

**Version:** 1.0-seed · **Owner:** Hermes (self-hosted standards & skills agent) ·
**Companion to:** [`REPO-STANDARDS.md`](REPO-STANDARDS.md)

The security posture every **self-hosted service** must meet to be *deployed* on
winterbottom.xyz infrastructure. Where `REPO-STANDARDS.md` measures a **repo** (branches,
tests, versioning, linting), this doc measures a **running service** — how it is exposed to
the network and how access to it is controlled.

> **Scope — deployment, not development.** These rules apply to anything **hosted** on the
> Docker hosts (`home-docker` — the always-on Pi + the single public Apache proxy —
> `desktop-docker`, `xps-docker`), i.e. anything with a service directory under
> `docker-infra/<host>/services/`. They do **not** apply to development-only artefacts —
> libraries, notebooks, devcontainers, or a repo that has no deployed surface. A thing is in
> scope the moment it is *hosted*, whether its image is **first-party** (built from an org
> repo) or **third-party** (an upstream image with no repo) — the two classes §6 of
> REPO-STANDARDS already distinguishes.

> **Seed.** This v1.0 is *seeded* from the security patterns already visible in `docker-infra`
> — the `entra-auth-proxy` sidecar in front of ComfyUI, the
> `AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / ALLOWED_EMAILS` OIDC pattern the
> first-party apps share, the `x-api-key` + OAuth gate on the MCP servers, and the public /
> internal / none split already governed by `scripts/tools-index.json`. Change the version and
> add a dated **Change log** note when a rule is adopted or amended.

Each section is checkable. The mechanical rules are enforced by
[`docker-infra/scripts/check-hosting-security.py`](https://github.com/DavidWinterbottom-2/docker-infra/blob/main/scripts/check-hosting-security.py)
(CI: `check-hosting-security`); the rest are reviewed against the service's actual compose /
proxy config.

---

## §H1 Every internet-reachable service authenticates

The Pi runs the **single public Apache reverse proxy**; a service is *public* when it is
reachable from the internet through it (its `scope` in `scripts/tools-index.json` is
`public`). **A `public` service either authenticates every request, or is *deliberately*
anonymous with a recorded rationale.** The default is to authenticate — via one of the
mechanisms in §H2 — before the request reaches the app. Anonymous (`anon`) is a conscious
exception, not the path of least resistance (§H2).

- **`internal`** services (LAN / Tailscale-only, reached by a `*-home` MagicDNS name) are not
  internet-exposed; the Tailscale network is the access boundary and they are exempt from the
  SSO requirement. Prefer moving a tool to `internal` over exposing it publicly when it has no
  real need to be on the internet.
- **`none`** services (databases, exporters, the proxy itself) have no web surface to
  authenticate.

**Checkable:** every `public` entry in `tools-index.json` declares an `auth` mechanism from
the §H2 vocabulary (or a recorded waiver, §H4). A public service with no declared auth fails
CI.

## §H2 Authentication is Microsoft / Entra — one identity provider

winterbottom.xyz has **one** identity provider: **Microsoft Entra ID (Azure AD)**, the same
single-tenant directory across every service. A service authenticates in exactly one of these
ways — the value it records as `auth` in the registry:

| `auth` value | What it means | Examples |
| --- | --- | --- |
| `entra-app` | **First-party** app doing OIDC **itself** via the shared `AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / ALLOWED_EMAILS` pattern. | `asset-summary`, `ynab-ingestor`, `german-story-generator`, `lisas-nutrition-tracker` |
| `entra-proxy` | App has **no auth of its own**, so it is fronted by the **`entra-auth-proxy` sidecar** (§H3) with no ports published on the app itself. | `comfyui` |
| `oauth` | **Third-party** app configured to use **native Microsoft/Entra OAuth** (its own OIDC integration, login form disabled). | `open-webui` |
| `app-native` | **Third-party** app enforcing its **own mandatory login** (credentialled, not anonymous). Acceptable where the upstream app owns authentication and Entra integration isn't available. | the `*arr` apps, `navidrome`, `audiobookshelf`, `immich`, `flatnotes` |
| `mcp` | **MCP server** gated by the `x-api-key` header **and** OAuth, per the `mcp-development` standard. | every `*-mcp` server |
| `anon` | **Intentionally public and unauthenticated** — a conscious decision that anonymous access is acceptable. Only justified when the service **holds no sensitive data** *and* **reaches no other internal service**. **Requires an `auth_rationale`** recording exactly that. | `every-day-calender` |

- New **first-party** services default to `entra-app` — wire it with the
  `entra-app-registration` skill (mints the app registration and emits the env values).
- A service that **cannot** do Entra itself and has **no login of its own** must use
  `entra-proxy` — never expose it raw.
- `app-native` is a deliberate acceptance that the upstream app owns auth; it is not a licence
  to run an anonymous app publicly. An upstream app with **no** authentication is either
  `entra-proxy` (fronted) or a justified `anon` — never left implicitly open.
- `anon` is the one way a public service may serve unauthenticated requests, and it is a
  **deliberate, recorded** choice — not an unfixed gap. It stands only while both conditions
  hold: **no sensitive data** and **no access to internal services**. The moment a service
  starts holding personal/sensitive data or gains a path to internal services, it must move to
  a real auth mechanism. The `auth_rationale` is mandatory so the reasoning is auditable. (A
  public service that *ought* to authenticate but doesn't yet is **not** `anon` — it's an
  `auth_waiver`, §H4.)

**Checkable:** `auth` is one of the values above. For `entra-proxy`, the service's
`docker-compose.yml` must reference the `entra-auth-proxy` image (§H3). For `anon`, a non-empty
`auth_rationale` is present; the check lists every `anon` service so the set stays visible.

## §H3 Sidecars live in the sidecar repo

Cross-cutting deployment helpers — the auth proxy above all — are **not hand-rolled inline per
service**. They are built once in
[`sidecar-containers`](https://github.com/DavidWinterbottom-2/sidecar-containers), published to
GHCR, and **consumed as a pinned image**:

```yaml
image: ghcr.io/davidwinterbottom-2/entra-auth-proxy:${ENTRA_AUTH_PROXY_VERSION:-latest}
```

- The Entra/OIDC defaults (provider, issuer, cookie-secure, scope, the unverified-email trust)
  are **baked into the sidecar image** in `sidecar-containers`; a consuming service supplies
  only its per-service values (`OAUTH2_PROXY_UPSTREAMS`, `OAUTH2_PROXY_REDIRECT_URL`, client id
  / secret / cookie secret from its `.env`). Don't re-derive that config in a raw
  `oauth2-proxy` block.
- The app being protected **publishes no host ports** — only the proxy is published, so the
  app is reachable *only* through it on the internal compose network (see the `comfyui` /
  `comfyui-auth` pair for the reference shape).

**Checkable:** a service whose `auth` is `entra-proxy` references
`ghcr.io/davidwinterbottom-2/entra-auth-proxy` (the sidecar-repo image), not a bespoke
`oauth2-proxy` configuration invented in that service's compose.

## §H4 One public edge, secrets from the environment

- **TLS terminates at the single public edge** — the Pi's Apache reverse proxy, in front of
  Cloudflare DNS. Services are reached through a `*.winterbottom.xyz` vhost that proxies to the
  app (or its auth proxy); services do not terminate their own public TLS or self-expose to the
  internet around the proxy.
- **Secrets never land in the repo.** Auth client ids, secrets and cookie secrets come from a
  git-ignored `.env` with a committed `.env.example` documenting every key (REPO-STANDARDS §5).
- **Recorded waiver ≠ anon.** Two different things must not be conflated. A justified `anon`
  service (§H2) is a **resting state** — it *should* be unauthenticated and has a rationale for
  why that's safe. A `auth_waiver` is a **tracked TODO** — a service that ought to authenticate
  but doesn't yet; it carries a string stating why and what the follow-up is, and its target is
  zero. The check surfaces `anon` services as an informational **note** and `auth_waiver`
  services as a **warning**, so both sets are always visible but only the waivers read as a gap
  to close.

**Checkable:** the check lists every `anon` service (note) and every `auth_waiver` service
(warning), so the accepted-anonymous set and the outstanding-gap set are both visible in CI
output.

---

## How Hermes uses this

Hermes reads a live checkout of `docker-infra`, not just the individual app repos, and audits
the deployment layer against the sections above:

- §H1 / §H2 — every `public` service in `tools-index.json` declares a valid `auth` (or a
  waiver); the deterministic gate is `check-hosting-security.py`.
- §H3 — `entra-proxy` services consume the sidecar-repo image rather than a hand-rolled proxy.
- §H4 — waivers are surfaced, not buried; the accepted-gap set trends to zero.

The `promote-to-service` skill wires a new service's auth (Entra app registration or the auth
proxy) and its `tools-index.json` classification together, so a service can't reach the public
page without a conscious auth decision.

## Change log

- **2026-07-27 — v1.0-seed.** Initial seed of the hosting-security standard, split out as its
  own doc (a deployment-layer concern, distinct from the per-repo `REPO-STANDARDS.md`). §H1 a
  public service either authenticates every request or is deliberately anonymous with a
  recorded rationale; §H2 auth is Microsoft/Entra via one of a fixed vocabulary (`entra-app` /
  `entra-proxy` / `oauth` / `app-native` / `mcp`), plus `anon` for an intentionally-public,
  no-sensitive-data / no-internal-access service (which requires an `auth_rationale`); §H3
  sidecars (the `entra-auth-proxy`) live in `sidecar-containers` and are consumed as a pinned
  image; §H4 one public TLS edge, secrets from `.env`, and — distinct from `anon` — an explicit
  recorded-waiver escape hatch for a service that ought to authenticate but doesn't yet.
  Enforced by `docker-infra/scripts/check-hosting-security.py` (CI: `check-hosting-security`),
  which extends the `tools-index.json` registry with an `auth` field per public service.
  Derived from existing infra practice (the ComfyUI `entra-auth-proxy` sidecar, the shared
  `AZURE_*` OIDC env pattern, the MCP `x-api-key` gate).
