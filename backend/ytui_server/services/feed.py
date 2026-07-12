"""RSS/Atom feed service: per-channel YouTube/BitChute feeds, merged and sorted.

Adapted from the ytui client's sources/rss.py; channels now come from the
server database instead of config.toml.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import feedparser
import httpx

from ..db import Database
from ..models import FollowedChannel, Video

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
HANDLE_URL = "https://www.youtube.com/{handle}"
BITCHUTE_PREFIX = "bitchute:"
BITCHUTE_RSS_URL = "https://api.bitchute.com/feeds/rss/channel/{channel_id}"
_BITCHUTE_EMBED_RE = re.compile(r"/embed/([\w-]+)/?")
TIMEOUT = httpx.Timeout(5.0)
CONCURRENCY = 10
_CHANNEL_ID_RE = re.compile(r'"channelId"\s*:\s*"(UC[\w-]{22})"')
_CANONICAL_RE = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"'
)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ytui-server/0.2"


class FeedResult:
    """Feed videos plus non-fatal warnings (failed channels, stale fallback)."""

    def __init__(self, videos: list[Video], warnings: list[str] | None = None) -> None:
        self.videos = videos
        self.warnings = warnings or []


def parse_rss(xml: str | bytes) -> list[Video]:
    """Parse a YouTube Atom feed into Video objects."""
    parsed = feedparser.parse(xml)
    videos: list[Video] = []
    for entry in parsed.entries:
        video_id = entry.get("yt_videoid") or ""
        if not video_id:
            continue
        published = None
        if entry.get("published_parsed"):
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        thumb = ""
        media = entry.get("media_thumbnail") or []
        if media:
            thumb = media[0].get("url", "")
        videos.append(
            Video(
                video_id=video_id,
                title=entry.get("title", ""),
                channel_title=entry.get("author", ""),
                channel_id=entry.get("yt_channelid", ""),
                published=published,
                thumbnail_url=thumb or f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
            )
        )
    return videos


def parse_bitchute_rss(xml: str | bytes, channel_id: str = "") -> list[Video]:
    """Parse a BitChute channel RSS feed into Video objects."""
    parsed = feedparser.parse(xml)
    channel_title = parsed.feed.get("title", "")
    videos: list[Video] = []
    for entry in parsed.entries:
        video_id = entry.get("id") or ""
        if not video_id:
            m = _BITCHUTE_EMBED_RE.search(entry.get("link", ""))
            video_id = m.group(1) if m else ""
        if not video_id:
            continue
        published = None
        if entry.get("published_parsed"):
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        thumb = ""
        enclosures = entry.get("enclosures") or []
        if enclosures:
            thumb = enclosures[0].get("href") or enclosures[0].get("url") or ""
        videos.append(
            Video(
                video_id=video_id,
                title=entry.get("title", ""),
                channel_title=channel_title,
                channel_id=channel_id,
                published=published,
                thumbnail_url=thumb,
                platform="bitchute",
            )
        )
    return videos


def extract_channel_id(html: str) -> str | None:
    """Extract a channel_id (UC...) from a YouTube channel page."""
    m = _CANONICAL_RE.search(html) or _CHANNEL_ID_RE.search(html)
    return m.group(1) if m else None


def merge_feeds(feeds: list[list[Video]]) -> list[Video]:
    """Merge per-channel video lists, newest first."""
    merged = [v for feed in feeds for v in feed]
    epoch = datetime.fromtimestamp(0, tz=timezone.utc)
    merged.sort(key=lambda v: v.published or epoch, reverse=True)
    return merged


class FeedService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def resolve_ref(self, ref: str, client: httpx.AsyncClient) -> FollowedChannel:
        """Resolve a channel reference (UC id, @handle or bitchute:slug)."""
        if ref.startswith(BITCHUTE_PREFIX):
            slug = ref[len(BITCHUTE_PREFIX):]
            return FollowedChannel(
                ref=ref, channel_id=slug, title=slug, platform="bitchute"
            )
        if ref.startswith("UC"):
            title = self.db.get_channel_name(ref) or ""
            return FollowedChannel(ref=ref, channel_id=ref, title=title)
        handle = ref if ref.startswith("@") else f"@{ref}"
        cached = self.db.get_handle(handle)
        if cached:
            title = self.db.get_channel_name(cached) or ""
            return FollowedChannel(ref=handle, channel_id=cached, title=title)
        resp = await client.get(
            HANDLE_URL.format(handle=handle),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        resp.raise_for_status()
        channel_id = extract_channel_id(resp.text)
        if not channel_id:
            raise ValueError(f"Could not resolve handle {handle!r} to a channel ID")
        self.db.set_handle(handle, channel_id)
        title = self.db.get_channel_name(channel_id) or ""
        return FollowedChannel(ref=handle, channel_id=channel_id, title=title)

    async def _fetch_channel(
        self,
        channel: FollowedChannel,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        force_refresh: bool,
    ) -> tuple[list[Video], str | None]:
        """Fetch one channel's feed. Returns (videos, warning)."""
        async with semaphore:
            if channel.platform == "bitchute":
                return await self._fetch_bitchute(channel, client, force_refresh)
            channel_id = channel.channel_id
            if not force_refresh:
                cached = self.db.get_feed(channel_id)
                if cached is not None:
                    return cached, None
            try:
                resp = await client.get(
                    RSS_URL.format(channel_id=channel_id),
                    headers={"User-Agent": USER_AGENT},
                )
                resp.raise_for_status()
                videos = parse_rss(resp.content)
                self.db.set_feed(channel_id, videos)
                return videos, None
            except Exception as exc:
                stale = self.db.get_feed(channel_id, allow_stale=True)
                if stale is not None:
                    return stale, f"{channel.ref}: using cached data ({type(exc).__name__})"
                return [], f"{channel.ref}: {exc}"

    async def _fetch_bitchute(
        self, channel: FollowedChannel, client: httpx.AsyncClient, force_refresh: bool
    ) -> tuple[list[Video], str | None]:
        cache_key = f"{BITCHUTE_PREFIX}{channel.channel_id}"
        if not force_refresh:
            cached = self.db.get_feed(cache_key)
            if cached is not None:
                return cached, None
        try:
            resp = await client.get(
                BITCHUTE_RSS_URL.format(channel_id=channel.channel_id),
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
            resp.raise_for_status()
            videos = parse_bitchute_rss(resp.content, channel_id=channel.channel_id)
            self.db.set_feed(cache_key, videos)
            return videos, None
        except Exception as exc:
            stale = self.db.get_feed(cache_key, allow_stale=True)
            if stale is not None:
                return stale, f"{channel.ref}: using cached data ({type(exc).__name__})"
            return [], f"{channel.ref}: {exc}"

    async def feed(self, force_refresh: bool = False) -> FeedResult:
        channels = self.db.list_channels()
        if not channels:
            return FeedResult([], [])
        semaphore = asyncio.Semaphore(CONCURRENCY)
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            results = await asyncio.gather(
                *(
                    self._fetch_channel(channel, client, semaphore, force_refresh)
                    for channel in channels
                )
            )
        feeds = [videos for videos, _ in results]
        warnings = [w for _, w in results if w]
        return FeedResult(merge_feeds(feeds), warnings)
