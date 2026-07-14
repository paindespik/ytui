#!/usr/bin/env python3
"""Desktop notification daemon for ytui.

Polls the backend feed every POLL_SECONDS, compares video IDs against a local
cache, and sends a notify-send for each new video.  Designed to run as a
systemd --user service — no TUI dependency.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import tomlkit

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POLL_SECONDS = 300  # 5 min — same as LIVE_POLL_SECONDS in the TUI
CACHE_FILE = Path.home() / ".cache" / "ytui" / "notify_seen.json"
CONFIG_FILE = Path.home() / ".config" / "ytui" / "config.toml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  ytui-notify  %(levelname)s  %(message)s",
)
log = logging.getLogger("ytui-notify")


def load_config() -> tuple[str, str]:
    """Return (server_url, api_token) from the TUI config.toml."""
    if not CONFIG_FILE.exists():
        sys.exit(f"Config not found: {CONFIG_FILE}")
    data = tomlkit.parse(CONFIG_FILE.read_text())
    server = data.get("server", {})
    url = server.get("url", "")
    token = server.get("token", "")
    if not url or not token:
        sys.exit("[server] url and token must be set in config.toml")
    return url, token


# ---------------------------------------------------------------------------
# Feed fetch
# ---------------------------------------------------------------------------

def fetch_new_videos(
    base_url: str, token: str, seen: set[str]
) -> tuple[bool, set[str], list[tuple[str, str]]]:
    """Fetch the feed.

    Returns (ok, updated_seen_set, list_of_new_(title, channel)).
    *ok* is False if the HTTP request or JSON parse failed.
    """
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = httpx.get(
            f"{base_url}/api/feed",
            headers=headers,
            timeout=30.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("feed request failed: %s", exc)
        return False, seen, []

    try:
        data = resp.json()
    except ValueError:
        log.error("invalid JSON response")
        return False, seen, []

    videos = data.get("videos", [])
    new: list[tuple[str, str]] = []
    for v in videos:
        vid = v.get("video_id", "")
        if vid and vid not in seen:
            seen.add(vid)
            new.append((v.get("title", ""), v.get("channel_title", "")))
    return True, seen, new


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_seen() -> set[str]:
    try:
        data = json.loads(CACHE_FILE.read_text())
        return set(data)
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen: set[str]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(list(seen)))


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

_NOTIFY_SEND = shutil.which("notify-send")


def notify(title: str, channel: str) -> None:
    if not _NOTIFY_SEND:
        log.info("notify-send not found — skipping notification for '%s'", title)
        return
    try:
        subprocess.run(
            [_NOTIFY_SEND, "--app-name=ytui", "--icon=video-display",
             f"📺 ytui — {channel}", title],
            capture_output=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("notify-send failed: %s", exc)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    base_url, token = load_config()
    seen = load_seen()

    # Seed the cache on first run so we don't flood with notifications.
    # Block until the seed fetch succeeds.
    while not seen:
        log.info("no cache — seeding with current feed (will retry if failed)")
        ok, seen, _ = fetch_new_videos(base_url, token, seen)
        if ok:
            save_seen(seen)
            break
        log.warning("seed failed, retrying in %ds", POLL_SECONDS)
        time.sleep(POLL_SECONDS)

    log.info("starting (poll=%ss, server=%s)", POLL_SECONDS, base_url)

    while True:
        ok, seen, new = fetch_new_videos(base_url, token, seen)
        if ok and new:
            for title, channel in new:
                log.info("new video: %s (%s)", title, channel)
                notify(title, channel)
            save_seen(seen)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
