# ytui

A self-hosted, multi-client YouTube (plus BitChute, Odysee, Twitch, TikTok and CrowdBunker) viewing system. A shared FastAPI backend aggregates RSS feeds, resolves playable stream URLs via `yt-dlp`, and stores history, playlists, and channels server-side in SQLite. Four independent front-ends — a Textual-based terminal UI, a headless argparse CLI, a Flutter Android app, and a browser SPA served by the backend itself — all consume the same REST API, so state (watch history, resume position, followed channels, local playlists) stays in sync across every device.

## Features

- **Unified feed** — merges YouTube, BitChute and Odysee RSS feeds plus the CrowdBunker posts API by publish date, with TTL-based caching (15 min) and stale-fallback on upstream failure.
- **Playback** — resolves the best available stream (HLS/progressive/DASH up to 4320p) via `yt-dlp`; the TUI drives `mpv` over JSON IPC, the mobile app uses `media_kit`.
- **Resolution cap** — one max height per client (360p…2160p, default 1440p), stored locally (TUI `config.toml`, mobile `SharedPreferences`, web `localStorage`) and changeable mid-playback from the player as well as from the settings screen. Honoured on all five platforms, VOD and live (a Twitch/YouTube master playlist is narrowed to a single variant, TikTok picks the best FLV quality under the cap); a source that only exists above the cap degrades to its lowest track instead of failing.
- **Cross-device continuity** — watch history, resume position (10s heartbeat), local playlists, and followed channels all live on the server, not per-client config.
- **Playlist import** — copy a whole upstream playlist (URL or id) into a local ytui playlist, either as a new playlist named after the upstream title or appended to an existing one (duplicates skipped). Available on every client: TUI (`S` on a playlist, `i` in local playlists), CLI (`ytui import`), mobile (playlist screen / long-press menu), web (playlist page and the Playlists tab).
- **Live notifications** — the server polls followed channels' `/live` pages every 5 min; clients poll `/api/lives` and surface desktop/mobile notifications, pinning live videos in the feed.
- **YouTube interactions** — an OAuth2 "token push" model: the desktop client completes OAuth consent and uploads the token to the server, which then acts for every client through the YouTube Data API v3 — like (a toggle, so it can be undone), read paginated comments, post one. In the mobile app and the browser SPA both live in the player: the comments panel takes over the queue/suggestions column without interrupting playback.
- **Search** — YouTube, Odysee and CrowdBunker search via `yt-dlp`/the LBRY API/the divulg.org search API, from any client.
- **Four clients, one backend** — a Textual TUI (with thumbnail rendering via sixel/kitty/Unicode fallback), an argparse CLI (`ytui play/search/import/auth`), a Flutter Android app (Riverpod + GoRouter + WorkManager background live polling), and a zero-build browser SPA (vanilla ES modules; dash.js/hls.js/mpegts.js playback through a same-origin stream proxy, server-generated DASH manifests for >360p YouTube, session-cookie auth, TUI-style keyboard shortcuts).

## Platform support

| Feature | YouTube | BitChute | Odysee | Twitch | TikTok | CrowdBunker |
|---|---|---|---|---|---|---|
| Followed-channel feed | ✅ RSS | ✅ RSS | ✅ RSS | ✅ (lives) | ✅ (lives) | ✅ posts API (2 pages) |
| Global search | ✅ yt-dlp | ❌ anti-bot | ✅ Lighthouse | ❌ | ❌ | ✅ search API |
| Channel videos | ✅ | ✅ | ✅ | ✅ VODs | ❌ lives only | ✅ |
| In-channel search | ✅ upstream | ✅ local scan | ✅ Lighthouse (≥3 chars) | ✅ local scan | — | ✅ local scan |
| VOD playback | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Lives | ✅ | ❌ | ❌ no API | ✅ ad-free proxies | ✅ webcast | ❌ no API |
| Live chat | ✅ | — | — | ✅ IRC | ❌ infeasible | — |
| Up next / related | ✅ scrape | ✅ same channel | ✅ same channel | ✅ same channel | ❌ | ✅ same channel |
| Comments | ✅ read+write (OAuth) | ❌ | ✅ read (Commentron) | ❌ | ❌ | ❌ |
| Likes | ✅ OAuth | ❌ | ❌ | ❌ | ❌ | ❌ |
| Upstream playlist import | ✅ | ✅ | ❌ 400 | ❌ 400 | ❌ | ❌ |
| SponsorBlock | ✅ | — | — | — | — | — |

Documented non-support (investigated, not planned):

- **BitChute search** sits behind a JavaScript anti-bot wall — not scrapable without a headless browser.
- **TikTok channel video pages** need authenticated scraping; channels surface through the lives tab only.
- **TikTok live chat** uses a signed WebSocket protocol — infeasible without official credentials.
- **Odysee and CrowdBunker lives** expose no public API to detect them.
- **Odysee likes/comments (write)** require an LBRY wallet signature.
- **Odysee playlists** (`odysee.com/$/playlist/…`) are a JS route with no yt-dlp extractor; **Twitch collections** have no stable public URL form — both answer an explicit 400.
- By design, **no authentication is added to non-YouTube providers**: everything above runs on public, no-auth endpoints.

## Architecture

```
Clients (TUI / CLI / Mobile) --Bearer-token REST--> FastAPI backend (uvicorn)
Browser SPA (session cookie) --same-origin REST-->  ├─ StaticFiles (ytui_server/web)
                                                    ├─ Stream proxy (/api/proxy, /api/proxy/hls)
                                                    ├─ DASH MPD generator (/api/videos/{id}/mpd)
                                                    ├─ FeedService (RSS merge)
                                                    ├─ YtdlpService (search/streams)
                                                    ├─ YouTubeService (OAuth2 like/comment)
                                                    ├─ OdyseeService (LBRY API)
                                                    └─ LiveMonitor (live polling)
                                                    → SQLite (meta.sqlite)

Deployment: Docker container behind nginx (TLS via Let's Encrypt), Forgejo CI/CD
            (backend-tests → build-apk → deploy via SSH + docker compose)
```

| Component | Stack |
|---|---|
| Backend  | Python 3.11+, FastAPI, yt-dlp, feedparser, httpx, pydantic, SQLite |
| TUI      | Python 3.11+, Textual, textual-image, mpv |
| CLI      | Python 3.11+, argparse, asyncio (shares the httpx REST client with the TUI) |
| Mobile   | Dart/Flutter 3.6+, Riverpod, GoRouter, dio, media_kit, flutter_local_notifications |
| Web      | Vanilla JS (ES modules, no build), dash.js, hls.js, mpegts.js — served by the backend |

## Setup

### Backend (server)

1. Install:
   ```bash
   cd backend && pip install -e .[auth]
   ```
   (Python 3.11+.)
2. Configure environment variables:
   - `YTUI_API_TOKEN` — Bearer token shared by all clients.
   - `YTUI_DATA_DIR` — SQLite + OAuth token storage location.
3. Run it, either directly:
   ```bash
   uvicorn ytui_server.main:app --host 0.0.0.0 --port 8776
   ```
   or via Docker:
   ```bash
   docker compose -f deploy/docker-compose.yml up -d
   ```
   (uses `deploy/Dockerfile`, mounts `deploy/data/` to `/data`).
4. Put nginx (see `deploy/nginx-ytui.conf`) in front for TLS termination. Health check available at `GET /health` (no auth required).

### TUI / CLI (desktop)

1. Install from the repo root (Python 3.11+; requires `mpv` installed on the system):
   ```bash
   pip install -e .
   ```
2. Configure `~/.config/ytui/config.toml`:
   ```toml
   [server]
   url = "https://ytui.example.com"
   token = "<YTUI_API_TOKEN>"

   [player]
   command = "mpv"
   max_height = 1440   # 360, 480, 720, 1080, 1440, 2160 — also settable with Q in the TUI
   ```
3. Launch the TUI:
   ```bash
   ytui
   ```
   or use headless commands:
   ```bash
   ytui play <url>
   ytui search <query>
   ytui import <playlist-url>
   ```
4. To enable YouTube like/comment actions:
   ```bash
   ytui auth push
   ```
   This runs OAuth consent in your browser and uploads the resulting token to the server.

### Mobile (Android)

1. Install dependencies:
   ```bash
   cd mobile && flutter pub get
   ```
2. On first launch, enter the backend server URL and Bearer token in Settings — the connection is verified against `/health` and `/api/history/watched-ids`.
3. Build a release APK:
   ```bash
   flutter build apk
   ```
   (also automated in CI).
4. On Android TV / projectors (leanback, no touchscreen) the app switches to a
   remote-driven player: ◀ ▶ seek ±10 s and raise the progress bar, OK toggles
   play/pause, ▼ walks the action row (speed, subtitles, audio offset) and Back
   dismisses the overlay. Focus highlights are forced on so the selection stays
   visible. If the sound lags the picture through the projector's own speakers,
   dial it in from the player's "Décalage audio" control — the offset is applied
   live (libmpv `--audio-delay`) and remembered per device.

### Web (browser)

Nothing to install: the SPA ships inside the backend wheel and is served at the
backend root (e.g. `https://ytui.example.com/`). Open it, enter the
`YTUI_API_TOKEN` on the login page (stored as an HttpOnly session cookie), and
everything works from there — including >360p YouTube playback via
server-generated DASH manifests proxied through the backend. Press `?` for the
keyboard shortcuts.

## Usage examples

```bash
# search across YouTube and Odysee
ytui search "linux kernel changelog"

# play a video directly by URL
ytui play https://youtu.be/dQw4w9WgXcQ

# copy a YouTube playlist into a local ytui playlist
ytui import "https://www.youtube.com/playlist?list=PLxxxx" --name "Guitar lessons"

# …or append it to an existing local playlist (id from the Playlists screen)
ytui import PLxxxx --into 3

# launch the full terminal UI
ytui

# push OAuth token so any client can like/comment as you
ytui auth push
```

## Development notes

- **Backend tests**: `cd backend && pytest` (11 files covering feed, history, channels, lives, streams, playlists, security).
- **TUI/CLI tests**: `pytest tests/` (7 files: app smoke, API client, models, config, mpv controller, thumbnails).
- **Mobile tests**: `cd mobile && flutter test` (API client, models).
- **Lint**: `ruff check` (Python), `flutter analyze` (Dart).
- CI/CD (Forgejo Actions) runs `backend-tests → build-apk → deploy via SSH + docker compose` on merge.
