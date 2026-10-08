# ytui-server

FastAPI backend for ytui. Holds all business logic: RSS feeds (YouTube + BitChute),
yt-dlp search/listings, stream URL resolution, watch history, local playlists,
followed channels, live detection and YouTube like/comment.

## Run locally

```sh
pip install -e .[dev]
YTUI_API_TOKEN=dev YTUI_DATA_DIR=./data uvicorn ytui_server.main:app --port 8776
```

## Configuration (environment)

| Variable | Default | Description |
|---|---|---|
| `YTUI_API_TOKEN` (or `YTUI_TOKEN`) | — (required) | Bearer token expected on every `/api/*` request |
| `YTUI_DATA_DIR` | `/data` | SQLite DB + OAuth token storage |
| `YTUI_FEED_TTL_MINUTES` | `15` | RSS feed cache TTL |
| `YTUI_LIVE_CHECK_MINUTES` | `5` | Live-detection poll interval |
| `YTUI_CLIENT_SECRET_PATH` | `$YTUI_DATA_DIR/client_secret.json` | Google OAuth client secret (like/comment) |
| `YTUI_SHARE_TTL_DAYS` | `7` | Lifetime of a share link (`0`: never expires) |

`GET /health` is public; everything under `/api/` requires
`Authorization: Bearer <token>`.

## Share links

`POST /api/shares {"video": {...}}` returns a private link `/s/{token}` that
plays **that video only**, without the token (web player page, any platform).
Re-sharing the same video returns the same link with its expiry pushed back;
a Twitch/TikTok live is pinned to the stream on air (the link reports the
live as ended afterwards, never the channel's next one). Every media URL the
public routes hand out is HMAC-signed for its link, so the share relay cannot
be used to reach anything else. `GET /api/shares` lists the live links,
`DELETE /api/shares/{token}` revokes one at once (page and media).

## Tests

```sh
cd backend && ruff check . && pytest
```
