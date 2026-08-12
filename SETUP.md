# MagicLists Setup Guide

Hands-on configuration and verification. For what the app does and how it's
deployed on winterbottom.xyz infrastructure, see [README.md](README.md); for repo
conventions, [AGENTS.md](AGENTS.md).

## Prerequisites

1. **A Navidrome server** with your music library scanned
2. **A Navidrome account** — username and password (MagicLists logs in and
   manages its own token; no manual API token setup)
3. **An AI API key** — optional, but curation quality depends on it

---

## 1. Environment

```bash
cp .env.example .env
```

[`.env.example`](.env.example) is the annotated, authoritative list. The
essentials:

```bash
# Required — Navidrome connection
NAVIDROME_URL=http://navidrome:4533     # container name on a shared Docker network
NAVIDROME_USERNAME=your_navidrome_username
NAVIDROME_PASSWORD=your_navidrome_password

# Required — database location (created automatically, must be writable)
DATABASE_PATH=/app/data/magiclists.db   # Docker, on a mounted volume
# DATABASE_PATH=./magiclists.db         # running from source

# Optional — AI curation (without it, playlists fall back to play-count ordering)
AI_PROVIDER=openrouter                  # openrouter | groq | google | ollama
AI_API_KEY=sk-or-v1-your-key-here       # not needed for ollama
AI_MODEL=meta-llama/llama-3.3-70b-instruct

# Optional — logging
LOG_LEVEL=INFO                          # ERROR | INFO | DEBUG
```

`.env` is git-ignored. Never commit it — document any new variable in
`.env.example` instead.

### Pick the right `NAVIDROME_URL`

| Navidrome is… | Use |
| --- | --- |
| on the same Docker network | `http://navidrome:4533` (its container name) |
| on the same host | `http://host.docker.internal:4533` (Docker Desktop) or `http://172.17.0.1:4533` (Linux) |
| elsewhere on the LAN | `http://192.168.1.100:4533` |
| public | `https://music.yourdomain.com` |

This is the single most common thing to get wrong.

---

## 2. Start it

### Option A — from source (development)

```bash
pip install -r requirements.txt
python -m uvicorn backend.main:app --host 0.0.0.0 --port 4545 --reload
```

Use `DATABASE_PATH=./magiclists.db` for this. Dev normally happens in the
devcontainer (`.devcontainer/`, "Reopen in Container") where dependencies are
already installed.

### Option B — Docker Compose

```bash
docker compose pull && docker compose up -d     # published GHCR image
docker compose up -d --build                    # build from source instead
```

The container listens on **4545** and needs `/app/data` on a persistent
volume — that's the SQLite database holding your playlists and schedules.

Either way, the app is at <http://localhost:4545>.

---

## 3. Verify

### System check page

Open <http://localhost:4545/system-check>. Configuration is also validated on
startup, and you'll be redirected here if anything required is wrong. It tests:

- **Environment variables** — required values present
- **Navidrome URL** — server reachable
- **Navidrome authentication** — credentials accepted
- **Navidrome artists API** — API access working
- **AI provider** — configured and reachable
- **Library configuration** — single vs multiple libraries
- **Last.fm integration** — optional; reported but never fails the page

Each failure comes with a specific suggestion. The same data is available as
JSON at `/api/health-check`, and a plain liveness probe at `/health`.

### Smoke-test the API

```bash
# Liveness
curl -f http://localhost:4545/health

# Library reachable
curl "http://localhost:4545/api/artists"

# Build a "This Is" playlist
curl -X POST "http://localhost:4545/api/create_playlist" \
  -H "Content-Type: application/json" \
  -d '{"artist_ids": ["<artist_id>"], "playlist_length": 25}'

# Build a Radio station from an artist seed
curl -X POST "http://localhost:4545/api/create_radio_playlist" \
  -H "Content-Type: application/json" \
  -d '{"seed_type": "artist", "seed_id": "<artist_id>", "playlist_length": 25}'

# ...or from a song seed (find one first)
curl "http://localhost:4545/api/songs?q=wintersleep"
curl -X POST "http://localhost:4545/api/create_radio_playlist" \
  -H "Content-Type: application/json" \
  -d '{"seed_type": "song", "seed_id": "<song_id>", "playlist_length": 25}'

# What was created, and how it was built
curl "http://localhost:4545/api/playlists"

# Scheduler
curl "http://localhost:4545/api/scheduler/status"
```

Interactive docs at <http://localhost:4545/docs>, schema at `/openapi.json`.

---

## 4. Optional integrations

### Last.fm

Sharpens curation across the board: loved tracks get a scoring boost in every
playlist type, Radio's album suggestions get grounded in the real
similar-artist graph, and Re-Discover gains a fallback.

```bash
LASTFM_API_KEY=          # create one at https://www.last.fm/api/account/create
LASTFM_USERNAME=         # your Last.fm handle, not your email
```

Reading your loved and top tracks needs only these two — no login — provided
Settings → Privacy → **"Hide recent listening information" is off**. Leave
`LASTFM_API_KEY` empty to disable; everything degrades to Navidrome-only.

### Lidarr

```bash
LIDARR_URL=https://lidarr.example.com
```

Radio's "albums you don't own yet" suggestions become one-click deep links into
Lidarr's *Add New* search, prefilled with the artist and album. Unset, they
render as plain text.

### Multiple Navidrome libraries

MagicLists detects and works across all libraries by default. To target one:

```bash
NAVIDROME_LIBRARY_ID=your-library-id-here
```

Find the ID in Navidrome's admin interface; the system-check page reports which
configuration is in effect.

### Analytics

Off unless **both** are set, and they should point at an Umami instance you run —
never a third party's, or your usage (including which AI provider and model you
use) leaves your estate.

```bash
ANALYTICS_SCRIPT_URL=https://<your-umami>/script.js
ANALYTICS_WEBSITE_ID=00000000-0000-0000-0000-000000000000
```

### Login gate (Microsoft Entra ID)

Off by default, so a trusted LAN or Tailscale deployment works unchanged. **Turn
it on before exposing the app to the internet** — it holds your Navidrome
credentials and AI key server-side.

```bash
AUTH_DISABLED=false
AZURE_TENANT_ID=consumers          # "consumers" for personal MS accounts, or a tenant GUID
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
ALLOWED_EMAILS=you@example.com     # comma-separated; empty = anyone in the tenant
SESSION_SECRET=<long random value> # set it, so logins survive a restart
```

Add a **Web** redirect URI of `https://<your-host>/auth/callback` to the Azure
app registration. With auth on and any credential missing, the app **refuses to
start** rather than coming up unprotected. See the README for the full contract.

---

## 5. How the features behave

### Playlist types

- **This Is (Artist)** — hits, deep cuts and featured appearances for one
  artist, without duplicates
- **Radio** — a station seeded from an artist or song, built from similar-style
  tracks in your own library, plus albums by fitting artists you don't own.
  Fully documented in the [README](README.md#radio-in-detail)
- **Genre Mix** — a curated mix from a whole genre in your collection
- **Re-Discover** — surfaces tracks you haven't played in a while

Default length is 25 tracks; 50 and 100 are offered in the UI.

### AI curation

- **With AI** — selection considers style, quality, variety and flow
- **Without AI** — falls back to play-count and recency ordering
- Radio benefits most: style coherence is a judgement call the fallback can't
  make

Rules that *must* hold are enforced in code after curation, not just asked for in
the prompt — so a Radio station always opens with its seed, never lets the seed
artist exceed 20% of the tracklist, and comes back genuinely **short** rather
than padded when your library runs out of similar material. The shortfall is
reported in the UI along with what to buy to fix it.

### Refreshing

- **Scheduled** — daily, weekly or monthly. The scheduler wakes twice a day
  (01:01 and 13:01) and rebuilds whatever is due
- **Catch-up** — a 7-day grace period covers refreshes missed while the app was
  offline
- **Recreate** — rebuild now from playlist management, optionally changing the
  length or schedule. It reuses the scheduler's own refresh path, so a manual
  rebuild and an automatic one produce identical results
- **Length is preserved** across refreshes unless you change it
- **How it was last built** is stored with the playlist and shown in the UI

```bash
curl "http://localhost:4545/api/scheduler/status"           # state
curl -X POST "http://localhost:4545/api/scheduler/start"    # start (auto-starts on launch)
curl -X POST "http://localhost:4545/api/scheduler/trigger"  # run the due-check now
curl -X POST "http://localhost:4545/api/playlists/1/recreate" \
  -H "Content-Type: application/json" -d '{"playlist_length": 50}'
```

Scheduler activity logs to stdout **and** a rotating `scheduler.log` (5MB × 2
backups) in the working directory. `LOG_LEVEL=DEBUG` makes it verbose.

### Storage

- **Navidrome** holds the real, playable playlists — with the curator's write-up
  as the playlist comment
- **The local SQLite database** holds metadata, track summaries, schedules,
  album suggestions and build records

---

## Troubleshooting

### "No artists found"

- Navidrome hasn't scanned the library, or is still scanning — check its logs
- `NAVIDROME_USERNAME` / `NAVIDROME_PASSWORD` wrong
- `NAVIDROME_URL` not reachable from *inside* the container

### Database write errors (500 on playlist creation, though system checks pass)

`DATABASE_PATH` is unset or not writable.

- **Docker** — `/app/data/magiclists.db`, with a volume mounted at `/app/data`
- **From source** — `./magiclists.db`, or an absolute path to a writable directory
- Check the directory exists, is writable by the app user (the container runs as
  uid 1000), and that there's disk space

### "Failed to create playlist"

- The artist has no tracks in the selected library
- The Navidrome account lacks playlist-creation permission
- Network between MagicLists and Navidrome is down — the error should say which

### AI curation not working

- `AI_PROVIDER` / `AI_API_KEY` wrong for the provider — the system-check page
  tests this
- OpenRouter: key out of credit. Groq / Google: key invalid
- Ollama: server not running (`ollama serve`), model not pulled, or the request
  timed out — raise `OLLAMA_TIMEOUT` (default 180s) on a slow CPU
- The app falls back to play-count selection rather than failing the request, so
  check `/api/ai-model-info` if you're unsure whether AI is actually in use

### Radio produces the same station every time

Check the build note on the playlist. If the similar-songs lookup failed, the
station was rebuilt from a reduced pool and will resemble the last one — a
Navidrome/Last.fm recall problem, not a curation one.

### Radio stations come up short

Your library doesn't have enough similar-style material for the requested
length. That's what the album suggestions beneath the tracklist are for.

### Containers can't see each other

```bash
docker network ls
docker network inspect <network>
docker ps --format "table {{.Names}}\t{{.Networks}}"
```

Both containers must be on the same network for the container-name URL to
resolve.

**Still stuck?** `/system-check` tests each dependency individually and tells you
what to fix.
