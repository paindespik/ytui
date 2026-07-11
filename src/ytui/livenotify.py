"""Live stream detection for followed channels and desktop notifications.

Detection uses the channel's /live page (one HTTP GET per channel, no yt-dlp).
Notifications go through `notify-send`; the click action is read back via
--wait, which blocks — call send_live_notification from a thread worker.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess

import httpx

from .models import Video

LIVE_URL = "https://www.youtube.com/channel/{channel_id}/live"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ytui/0.1"

# A live (or premiere) /live page canonicalizes to a watch URL; an idle channel
# canonicalizes back to the channel page.
_CANONICAL_WATCH_RE = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"'
)
_IS_LIVE_RE = re.compile(r'"isLive"\s*:\s*true')
_IS_UPCOMING_RE = re.compile(r'"isUpcoming"\s*:\s*true')
_TITLE_RE = re.compile(r'<meta (?:name="title"|property="og:title") content="([^"]*)"')


def parse_live_page(page: str, channel_id: str, channel_title: str = "") -> Video | None:
    """Return the currently-live Video from a /live page, or None."""
    m = _CANONICAL_WATCH_RE.search(page)
    if not m:
        return None  # no live: canonical points back to the channel
    if not _IS_LIVE_RE.search(page) or _IS_UPCOMING_RE.search(page):
        return None  # scheduled/upcoming stream, not started yet
    tm = _TITLE_RE.search(page)
    title = html.unescape(tm.group(1)) if tm else "Live"
    return Video(
        video_id=m.group(1),
        title=title,
        channel_id=channel_id,
        channel_title=channel_title,
    )


async def check_channel_live(
    client: httpx.AsyncClient, channel_id: str, channel_title: str = ""
) -> Video | None:
    """Fetch the channel's /live page and return the live Video, or None."""
    resp = await client.get(
        LIVE_URL.format(channel_id=channel_id),
        headers={"User-Agent": USER_AGENT},
        # SOCS bypasses the EU consent interstitial redirect.
        cookies={"SOCS": "CAI"},
        follow_redirects=True,
    )
    resp.raise_for_status()
    return parse_live_page(resp.text, channel_id, channel_title)


def send_live_notification(video: Video) -> bool:
    """Blocking desktop notification. True if the user clicked the action."""
    if shutil.which("notify-send") is None:
        return False
    cmd = [
        "notify-send",
        "--app-name=ytui",
        "--icon=video-display",
        "--wait",
        "--action=default=Ouvrir dans ytui",
        f"🔴 Live — {video.channel_title or 'YouTube'}",
        video.title,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return False
    if res.returncode != 0:
        # Old libnotify without --wait/--action: fire-and-forget fallback.
        subprocess.run(cmd[:3] + cmd[5:], capture_output=True, text=True)
        return False
    return bool(res.stdout.strip())
