"""Periodic live-stream detection for followed YouTube channels.

Adapted from the ytui client's livenotify.py (detection only, no notify-send —
clients poll GET /api/lives).
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from datetime import datetime, timezone

import httpx

from ..db import Database
from ..models import Video

log = logging.getLogger(__name__)

LIVE_URL = "https://www.youtube.com/channel/{channel_id}/live"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ytui-server/0.2"

_CANONICAL_WATCH_RE = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"'
)
_IS_LIVE_RE = re.compile(r'"isLive"\s*:\s*true')
_IS_UPCOMING_RE = re.compile(r'"isUpcoming"\s*:\s*true')
_TITLE_RE = re.compile(r'<meta (?:name="title"|property="og:title") content="([^"]*)"')
# Channel name from ytInitialPlayerResponse.videoDetails — same JSON block as
# "isLive", so any page that matches _IS_LIVE_RE also carries it.
_AUTHOR_RE = re.compile(r'"author"\s*:\s*"((?:[^"\\]|\\.)*)"')


def parse_live_page(page: str, channel_id: str, channel_title: str = "") -> Video | None:
    """Return the currently-live Video from a /live page, or None."""
    m = _CANONICAL_WATCH_RE.search(page)
    if not m:
        return None
    if not _IS_LIVE_RE.search(page) or _IS_UPCOMING_RE.search(page):
        return None
    tm = _TITLE_RE.search(page)
    title = html.unescape(tm.group(1)) if tm else "Live"
    if not channel_title:
        am = _AUTHOR_RE.search(page)
        if am:
            try:
                channel_title = json.loads(f'"{am.group(1)}"')
            except ValueError:
                channel_title = am.group(1)
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


class LiveMonitor:
    """In-memory map of currently-live videos, refreshed periodically."""

    def __init__(self, db: Database, check_minutes: int = 5) -> None:
        self.db = db
        self.check_minutes = check_minutes
        self.lives: dict[str, tuple[Video, datetime]] = {}
        self._task: asyncio.Task | None = None

    async def check_once(self) -> None:
        channels = [c for c in self.db.list_channels() if c.platform == "youtube"]
        if not channels:
            self.lives = {}
            return
        sem = asyncio.Semaphore(10)

        async def check(channel) -> tuple[str, str, Video | None]:
            async with sem:
                try:
                    video = await check_channel_live(
                        client, channel.channel_id, channel.title
                    )
                except Exception as exc:
                    log.debug("live check failed for %s: %s", channel.channel_id, exc)
                    video = None
                return channel.channel_id, channel.title, video

        current: dict[str, tuple[Video, datetime]] = {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            results = await asyncio.gather(*(check(c) for c in channels))
        for channel_id, _title, video in results:
            if video is None:
                continue
            previous = self.lives.get(channel_id)
            detected_at = (
                previous[1]
                if previous and previous[0].video_id == video.video_id
                else datetime.now(tz=timezone.utc)
            )
            current[channel_id] = (video, detected_at)
        self.lives = current

    async def _loop(self) -> None:
        while True:
            try:
                await self.check_once()
            except Exception:
                log.exception("live poll iteration failed")
            await asyncio.sleep(self.check_minutes * 60)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
