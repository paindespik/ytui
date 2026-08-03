"""Home suggestions: the account's YouTube home feed, else history-based related videos.

With account cookies pushed to the server (see services/cookies.py) the feed is
YouTube's own personalised home page. Without them, the anonymous fallback takes
the last few YouTube videos from watch history as seeds, fetches their related
videos in parallel, then interleaves the sources round-robin for diversity.
"""

from __future__ import annotations

import asyncio
import logging

from ..db import Database
from ..models import Video
from . import ytdlp
from .cookies import CookieStore
from .feed import FeedResult

SEED_COUNT = 8
RELATED_PER_SEED = 20
MAX_SUGGESTIONS = 50
CACHE_KEY = "suggestions:home"

EMPTY_HISTORY_WARNING = "No watch history yet: watch a few videos to get suggestions"
COOKIE_STALE_WARNING = (
    "YouTube rejected the account cookies: run `ytui auth cookies` again "
    "(falling back to history-based suggestions)"
)

log = logging.getLogger(__name__)


def _pick_seeds(history: list[Video], count: int = SEED_COUNT) -> list[Video]:
    """Most recent YouTube videos, deduplicated by channel for seed diversity."""
    seeds: list[Video] = []
    seen_channels: set[str] = set()
    for video in history:
        if video.kind != "video" or video.platform != "youtube":
            continue
        channel_key = video.channel_id or video.channel_title
        if channel_key and channel_key in seen_channels:
            continue
        if channel_key:
            seen_channels.add(channel_key)
        seeds.append(video)
        if len(seeds) >= count:
            break
    return seeds


def _interleave(
    sources: list[list[Video]], excluded_ids: set[str], cap: int = MAX_SUGGESTIONS
) -> list[Video]:
    """Round-robin merge, deduplicated by video_id, excluding known ids."""
    merged: list[Video] = []
    seen: set[str] = set(excluded_ids)
    for tier in range(max((len(s) for s in sources), default=0)):
        for source in sources:
            if tier >= len(source):
                continue
            video = source[tier]
            if video.video_id in seen:
                continue
            seen.add(video.video_id)
            merged.append(video)
            if len(merged) >= cap:
                return merged
    return merged


class SuggestionsService:
    def __init__(
        self,
        db: Database,
        ttl_seconds: int = 30 * 60,
        cookies: CookieStore | None = None,
        keepalive_hours: int = 0,
    ) -> None:
        self.db = db
        self.ttl_seconds = ttl_seconds
        self.cookies = cookies
        self.keepalive_hours = keepalive_hours
        self._keepalive: asyncio.Task | None = None

    def start(self) -> None:
        """Start the cookie keep-alive task (no-op without cookies or interval)."""
        if (
            self._keepalive is None
            and self.keepalive_hours > 0
            and self.cookies is not None
        ):
            self._keepalive = asyncio.get_running_loop().create_task(self._keepalive_loop())

    async def stop(self) -> None:
        task, self._keepalive = self._keepalive, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _keepalive_loop(self) -> None:
        """Periodic authenticated request so Google rotates (and yt-dlp rewrites)
        the stored cookies instead of letting the session lapse."""
        assert self.cookies is not None
        while True:
            await asyncio.sleep(self.keepalive_hours * 3600)
            if not self.cookies.exists():
                continue
            try:
                videos = await ytdlp.home_recommendations(self.cookies.path, limit=1)
            except ytdlp.UpstreamError as exc:
                log.warning("cookie keep-alive failed: %s", exc)
                continue
            if videos:
                log.info("cookie session refreshed")
            else:
                log.warning("cookie keep-alive got no videos: session may be stale")

    async def suggestions(self, force_refresh: bool = False) -> FeedResult:
        if not force_refresh:
            cached = self.db.get_feed(CACHE_KEY, ttl_seconds=self.ttl_seconds)
            if cached is not None:
                return FeedResult(cached, [])

        stale_warning: str | None = None
        if self.cookies is not None and self.cookies.exists():
            try:
                videos = await ytdlp.home_recommendations(
                    self.cookies.path, limit=MAX_SUGGESTIONS
                )
            except ytdlp.UpstreamError as exc:
                log.warning("home recommendations failed: %s", exc)
                videos = []
            if videos:
                self.db.set_feed(CACHE_KEY, videos)
                return FeedResult(videos)
            stale_warning = COOKIE_STALE_WARNING

        history = [video for video, _, _ in self.db.watch_history(limit=100)]
        seeds = _pick_seeds(history)
        prefix = [stale_warning] if stale_warning else []
        if not seeds:
            return FeedResult([], [*prefix, EMPTY_HISTORY_WARNING])

        results = await asyncio.gather(
            *(ytdlp.related_videos(s.video_id, limit=RELATED_PER_SEED) for s in seeds),
            return_exceptions=True,
        )
        sources: list[list[Video]] = []
        warnings: list[str] = list(prefix)
        for seed, result in zip(seeds, results):
            if isinstance(result, BaseException):
                warnings.append(f"{seed.title or seed.video_id}: {result}")
            else:
                sources.append(result)

        excluded = {v.video_id for v in history} | {s.video_id for s in seeds}
        videos = _interleave(sources, excluded)
        if videos:
            self.db.set_feed(CACHE_KEY, videos)
        elif not warnings:
            warnings.append(EMPTY_HISTORY_WARNING)
        log.info(
            "suggestions: %d seeds, %d videos, %d warnings",
            len(seeds), len(videos), len(warnings),
        )
        return FeedResult(videos, warnings)
