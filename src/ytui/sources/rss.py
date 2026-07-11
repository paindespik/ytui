"""RSS/Atom feed source: per-channel YouTube feeds, merged and sorted by date."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import feedparser
import httpx

from ..cache import MetaCache
from ..models import Video
from .base import FeedResult, VideoSource

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
HANDLE_URL = "https://www.youtube.com/{handle}"
BITCHUTE_PREFIX = "bitchute:"
BITCHUTE_RSS_URL = "https://api.bitchute.com/feeds/rss/channel/{channel_id}"
_BITCHUTE_EMBED_RE = re.compile(r"/embed/([\w-]+)/?")
TIMEOUT = httpx.Timeout(5.0)
CONCURRENCY = 10
_CHANNEL_ID_RE = re.compile(r'"channelId"\s*:\s*"(UC[\w-]{22})"')
_CANONICAL_RE = re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"')

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ytui/0.1"


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


class RSSSource(VideoSource):
    def __init__(self, channels: list[str], cache: MetaCache) -> None:
        self.channels = channels
        self.cache = cache

    async def resolve_channel(self, entry: str, client: httpx.AsyncClient) -> str:
        """Resolve a config entry (UC id or @handle) to a channel_id."""
        if entry.startswith("UC"):
            return entry
        handle = entry if entry.startswith("@") else f"@{entry}"
        cached = self.cache.get_handle(handle)
        if cached:
            return cached
        resp = await client.get(
            HANDLE_URL.format(handle=handle),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        resp.raise_for_status()
        channel_id = extract_channel_id(resp.text)
        if not channel_id:
            raise ValueError(f"Could not resolve handle {handle!r} to a channel ID")
        self.cache.set_handle(handle, channel_id)
        return channel_id

    async def _fetch_channel(
        self,
        entry: str,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        force_refresh: bool,
    ) -> tuple[list[Video], str | None]:
        """Fetch one channel's feed. Returns (videos, warning)."""
        async with semaphore:
            if entry.startswith(BITCHUTE_PREFIX):
                return await self._fetch_bitchute(entry, client, force_refresh)
            try:
                channel_id = await self.resolve_channel(entry, client)
            except Exception as exc:
                return [], f"{entry}: {exc}"
            if not force_refresh:
                cached = self.cache.get_feed(channel_id)
                if cached is not None:
                    return cached, None
            try:
                resp = await client.get(
                    RSS_URL.format(channel_id=channel_id),
                    headers={"User-Agent": USER_AGENT},
                )
                resp.raise_for_status()
                videos = parse_rss(resp.content)
                self.cache.set_feed(channel_id, videos)
                return videos, None
            except Exception as exc:
                # Offline / error fallback: serve stale cache with a warning.
                stale = self.cache.get_feed(channel_id, allow_stale=True)
                if stale is not None:
                    return stale, f"{entry}: using cached data ({type(exc).__name__})"
                return [], f"{entry}: {exc}"

    async def _fetch_bitchute(
        self, entry: str, client: httpx.AsyncClient, force_refresh: bool
    ) -> tuple[list[Video], str | None]:
        """Fetch one BitChute channel's feed; cached under the 'bitchute:<slug>' key."""
        name = entry[len(BITCHUTE_PREFIX):]
        if not force_refresh:
            cached = self.cache.get_feed(entry)
            if cached is not None:
                return cached, None
        try:
            resp = await client.get(
                BITCHUTE_RSS_URL.format(channel_id=name),
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
            resp.raise_for_status()
            videos = parse_bitchute_rss(resp.content, channel_id=name)
            self.cache.set_feed(entry, videos)
            return videos, None
        except Exception as exc:
            stale = self.cache.get_feed(entry, allow_stale=True)
            if stale is not None:
                return stale, f"{entry}: using cached data ({type(exc).__name__})"
            return [], f"{entry}: {exc}"

    async def feed(self, force_refresh: bool = False) -> FeedResult:
        semaphore = asyncio.Semaphore(CONCURRENCY)
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            results = await asyncio.gather(
                *(
                    self._fetch_channel(entry, client, semaphore, force_refresh)
                    for entry in self.channels
                )
            )
        feeds = [videos for videos, _ in results]
        warnings = [w for _, w in results if w]
        return FeedResult(merge_feeds(feeds), warnings)

    async def search(self, query: str, limit: int = 20) -> list[Video]:
        raise NotImplementedError("Search is provided by the yt-dlp source")
