#!/usr/bin/env python3
"""Desktop notification daemon for ytui.

Polls the backend every POLL_SECONDS for new feed videos (/api/feed) and
live streams (/api/lives), compares IDs against a local cache, and sends a
notify-send for each new item.  Notifications carry two actions:
"Regarder" (play in mpv via `ytui play`) and "Ouvrir ytui" (open the TUI in
a terminal).  Designed to run as a systemd --user service — no TUI import.

Dedup conventions (shared with the TUI and the mobile app):
- Feed videos are seeded silently on first run (no notification flood).
- Lives are never seeded: a stream that is live when the daemon starts is
  news worth notifying.  A seen live ID is only forgotten after it has been
  absent from /api/lives for LIVE_FORGET_POLLS consecutive polls — so a
  channel going live again later re-notifies, but a transient empty
  response (backend upstream blip) cannot re-notify a still-running stream.
"""

from __future__ import annotations

import json
import logging
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import tomlkit

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POLL_SECONDS = 60  # Twitch/TikTok lives are caught ~1 min server-side → notify ≤ ~2 min in
NOTIFY_WAIT_TIMEOUT = 3600  # reap a --wait notify-send the server never closes (wedged daemon)
CACHE_FILE = Path.home() / ".cache" / "ytui" / "notify_seen.json"
CONFIG_FILE = Path.home() / ".config" / "ytui" / "config.toml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  ytui-notify  %(levelname)s  %(message)s",
)
log = logging.getLogger("ytui-notify")


def load_config() -> tuple[str, str, str]:
    """Return (server_url, api_token, terminal_command) from the TUI config.toml.

    terminal_command is the optional `[notify] terminal` override (a command
    prefix such as "foot" or "alacritty -e"); empty string means auto-detect.
    """
    if not CONFIG_FILE.exists():
        sys.exit(f"Config not found: {CONFIG_FILE}")
    data = tomlkit.parse(CONFIG_FILE.read_text())
    server = data.get("server", {})
    url = server.get("url", "")
    token = server.get("token", "")
    if not url or not token:
        sys.exit("[server] url and token must be set in config.toml")
    notify_cfg = data.get("notify", {})
    return url, token, str(notify_cfg.get("terminal", ""))


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_new_videos(
    base_url: str, token: str, seen: set[str]
) -> tuple[bool, set[str], list[dict[str, str]]]:
    """Fetch the feed.

    Returns (ok, updated_seen_set, new_items); each new item is a dict with
    "title", "channel" and "url".  *ok* is False if the HTTP request or the
    JSON parse failed.
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

    new: list[dict[str, str]] = []
    for v in data.get("videos", []):
        vid = v.get("video_id", "")
        key = f"{v.get('platform', 'youtube')}:{vid}"
        # New ids are stored platform-qualified; the bare-id check keeps the
        # pre-migration cache entries deduplicating (no notification flood).
        if vid and key not in seen and vid not in seen:
            seen.add(key)
            new.append(
                {
                    "title": v.get("title", ""),
                    "channel": v.get("channel_title", ""),
                    "url": v.get("url", ""),
                    "platform": v.get("platform", "youtube"),
                }
            )
    return True, seen, new


def fetch_lives(base_url: str, token: str) -> tuple[bool, dict[str, dict[str, str]]]:
    """Fetch current live streams.

    Returns (ok, {video_id: {"title", "channel", "url"}}).
    """
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = httpx.get(
            f"{base_url}/api/lives",
            headers=headers,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("lives request failed: %s", exc)
        return False, {}

    current: dict[str, dict[str, str]] = {}
    for entry in data:
        v = entry.get("video", {})
        vid = v.get("video_id", "")
        if vid:
            # Platform-qualified key: the same bare id can exist on two platforms.
            current[f"{v.get('platform', 'youtube')}:{vid}"] = {
                "title": v.get("title", ""),
                "channel": v.get("channel_title", ""),
                "url": v.get("url", ""),
                "platform": v.get("platform", "youtube"),
            }
    return True, current


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

LIVE_FORGET_POLLS = 3  # forget a live id after ~15 min of consecutive absence


def load_cache() -> tuple[set[str], dict[str, int]]:
    """Return (seen_video_ids, {seen_live_id: consecutive_absent_polls}).

    Accepts the legacy flat-list format (feed video ids only).
    """
    try:
        data = json.loads(CACHE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return set(), {}
    if isinstance(data, list):  # legacy format: list of feed video ids
        return set(data), {}
    lives_raw = data.get("lives", {})
    if isinstance(lives_raw, list):
        lives = dict.fromkeys(lives_raw, 0)
    else:
        lives = {str(vid): int(misses) for vid, misses in lives_raw.items()}
    return set(data.get("videos", [])), lives


def save_cache(videos: set[str], lives: dict[str, int]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps({"videos": sorted(videos), "lives": lives}))


# ---------------------------------------------------------------------------
# Notification actions
# ---------------------------------------------------------------------------

_NOTIFY_SEND = shutil.which("notify-send")

# Body click ("default") behaves like the explicit "Ouvrir ytui" button.
_ACTION_ARGS = (
    "--action=watch=▶ Regarder",
    "--action=open=Ouvrir ytui",
    "--action=default=Ouvrir ytui",
)

# (binary, exec flags) — first match wins when [notify] terminal is not set.
_TERMINALS = (
    ("foot", ()),
    ("kitty", ()),
    ("alacritty", ("-e",)),
    ("wezterm", ("start", "--")),
    ("ghostty", ("-e",)),
    ("konsole", ("-e",)),
    ("gnome-terminal", ("--",)),
    ("xfce4-terminal", ("-x",)),
    ("xterm", ("-e",)),
)


def _spawn(cmd: list[str]) -> None:
    """Launch a GUI process detached from the daemon."""
    log.info("launching: %s", " ".join(cmd))
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        log.warning("failed to launch %s: %s", cmd[0], exc)


def _ytui_bin() -> str | None:
    found = shutil.which("ytui")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "ytui"
    return str(local) if local.exists() else None


def launch_player(url: str) -> None:
    """"Regarder": play through `ytui play` (honours [player] config) or bare mpv."""
    ytui = _ytui_bin()
    if ytui:
        _spawn([ytui, "play", url])
        return
    mpv = shutil.which("mpv")
    if mpv:
        _spawn([mpv, url])
        return
    log.warning("neither ytui nor mpv found — cannot play %s", url)


def launch_ytui(terminal: str) -> None:
    """"Ouvrir ytui": run the TUI in a terminal emulator."""
    ytui = _ytui_bin()
    if not ytui:
        log.warning("ytui executable not found")
        return
    if terminal:
        _spawn([*shlex.split(terminal), ytui])
        return
    xdg = shutil.which("xdg-terminal-exec")
    if xdg:
        _spawn([xdg, ytui])
        return
    for name, flags in _TERMINALS:
        binpath = shutil.which(name)
        if binpath:
            _spawn([binpath, *flags, ytui])
            return
    log.warning("no terminal emulator found to open ytui")


def notify(summary: str, body: str, url: str, terminal: str) -> None:
    """Show an actionable notification; the click is handled in a daemon thread."""
    if not _NOTIFY_SEND:
        log.info("notify-send not found — skipping notification for '%s'", body)
        return
    threading.Thread(
        target=_notify_and_dispatch,
        args=(summary, body, url, terminal),
        daemon=True,
    ).start()


def _notify_and_dispatch(summary: str, body: str, url: str, terminal: str) -> None:
    """Blocks until the notification is closed, then runs the chosen action."""
    cmd = [
        _NOTIFY_SEND,
        "--app-name=ytui",
        "--icon=video-display",
        "--wait",
        *_ACTION_ARGS,
        summary,
        body,
    ]
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=NOTIFY_WAIT_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        log.warning(
            "notify-send --wait exceeded %ss (notification server unresponsive?) — reaped",
            NOTIFY_WAIT_TIMEOUT,
        )
        return
    except OSError as exc:
        log.warning("notify-send failed: %s", exc)
        return
    if res.returncode != 0:
        # Old libnotify without --wait/--action: fire-and-forget fallback.
        plain = [a for a in cmd if a != "--wait" and not a.startswith("--action=")]
        try:
            subprocess.run(plain, capture_output=True, timeout=10)
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.warning("notify-send fallback failed: %s", exc)
        return
    action = res.stdout.strip()
    if not action:
        return  # dismissed or expired
    if action == "watch" and url:
        launch_player(url)
    else:
        launch_ytui(terminal)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    base_url, token, terminal = load_config()
    videos_seen, lives_seen = load_cache()

    # Seed the feed cache on first run so we don't flood with notifications.
    # Block until the seed fetch succeeds.  Lives are intentionally NOT
    # seeded: an ongoing stream is always worth one notification.
    while not videos_seen:
        log.info("no cache — seeding with current feed (will retry if failed)")
        ok, videos_seen, _ = fetch_new_videos(base_url, token, videos_seen)
        if ok:
            save_cache(videos_seen, lives_seen)
            break
        log.warning("seed failed, retrying in %ds", POLL_SECONDS)
        time.sleep(POLL_SECONDS)

    log.info("starting (poll=%ss, server=%s)", POLL_SECONDS, base_url)

    while True:
        ok, videos_seen, new = fetch_new_videos(base_url, token, videos_seen)
        if ok and new:
            for item in new:
                log.info("new video: %s (%s)", item["title"], item["channel"])
                notify(
                    f"📺 ytui — {item['channel']}", item["title"], item["url"], terminal
                )
            save_cache(videos_seen, lives_seen)

        ok, current = fetch_lives(base_url, token)
        if ok:
            for vid, item in current.items():
                # `vid` is qualified; the bare-id check covers cache entries
                # written before the qualified-key migration.
                bare = vid.split(":", 1)[1]
                if vid not in lives_seen and bare not in lives_seen:
                    log.info("live: %s (%s)", item["title"], item["channel"])
                    platform = item.get("platform", "youtube")
                    icon = {"twitch": "🟣", "tiktok": "🎵"}.get(platform, "🔴")
                    label = {"twitch": "Twitch", "tiktok": "TikTok"}.get(platform, "Live")
                    notify(
                        f"{icon} {label} — {item['channel'] or 'YouTube'}",
                        item["title"],
                        item["url"],
                        terminal,
                    )
            # Grace period: ids absent from the response age instead of being
            # dropped immediately, so one empty poll can't cause a duplicate
            # notification for a stream that is still running.
            updated = dict.fromkeys(current, 0)
            for vid, misses in lives_seen.items():
                if vid not in current and misses + 1 < LIVE_FORGET_POLLS:
                    updated[vid] = misses + 1
            if updated != lives_seen:
                lives_seen = updated
                save_cache(videos_seen, lives_seen)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
