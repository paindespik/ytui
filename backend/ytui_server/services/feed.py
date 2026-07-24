"""RSS/Atom feed service: per-channel YouTube/BitChute feeds, merged and sorted.

Adapted from the ytui client's sources/rss.py; channels now come from the
server database instead of config.toml.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

import feedparser
import httpx

from ..db import Database
from ..models import FollowedChannel, Video
from . import odysee, tiktok, twitch

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
HANDLE_URL = "https://www.youtube.com/{handle}"
BITCHUTE_PREFIX = "bitchute:"
BITCHUTE_RSS_URL = "https://api.bitchute.com/feeds/rss/channel/{channel_id}"
_BITCHUTE_EMBED_RE = re.compile(r"/embed/([\w-]+)/?")
ODYSEE_PREFIX = "odysee:"
ODYSEE_RSS_URL = "https://odysee.com/$/rss/{channel_id}"
TWITCH_PREFIX = "twitch:"
TIKTOK_PREFIX = "tiktok:"
_ODYSEE_LINK_RE = re.compile(r"odysee\.com/(?:@[^/]+/)?([^/?#]+:[0-9a-f]+)")
_ODYSEE_CHANNEL_URL_RE = re.compile(r"odysee\.com/(@[^/?#:]+:\w+)")
_IMG_SRC_RE = re.compile(r'<img[^>]+src="([^"]+)"')
TIMEOUT = httpx.Timeout(5.0)
CONCURRENCY = 10
_CHANNEL_ID_RE = re.compile(r'"channelId"\s*:\s*"(UC[\w-]{22})"')
_CANONICAL_RE = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"'
)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ytui-server/0.2"

log = logging.getLogger(__name__)


async def _get_with_retry(
    client: httpx.AsyncClient, url: str, *, attempts: int = 2, **kwargs
) -> httpx.Response:
    """GET with one retry on transport errors and 5xx (upstream blips)."""
    for attempt in range(attempts):
        try:
            resp = await client.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            retryable = (
                isinstance(exc, httpx.TransportError)
                or exc.response.status_code >= 500
            )
            if attempt + 1 == attempts or not retryable:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))
    raise AssertionError("unreachable")


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


def parse_odysee_rss(xml: str | bytes, channel_id: str = "") -> list[Video]:
    """Parse an Odysee channel RSS feed into Video objects."""
    parsed = feedparser.parse(xml)
    channel_title = parsed.feed.get("title", "")
    videos: list[Video] = []
    for entry in parsed.entries:
        m = _ODYSEE_LINK_RE.search(entry.get("link", ""))
        if not m:
            continue
        video_id = m.group(1)
        published = None
        if entry.get("published_parsed"):
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        thumb = ""
        media = entry.get("media_thumbnail") or []
        if media:
            thumb = media[0].get("url", "")
        if not thumb:
            # Odysee embeds the thumbnail as an <img> in the item description HTML
            m_img = _IMG_SRC_RE.search(entry.get("summary", "") or entry.get("description", ""))
            if m_img:
                thumb = m_img.group(1)
        videos.append(
            Video(
                video_id=video_id,
                title=entry.get("title", ""),
                channel_title=channel_title,
                channel_id=channel_id,
                published=published,
                thumbnail_url=thumb,
                platform="odysee",
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
        """Resolve a channel ref: UC id, @handle, bitchute:/odysee:/twitch:/
        tiktok: prefix, or a bare name (YouTube handle first, then Twitch login)."""
        if ref.startswith(BITCHUTE_PREFIX):
            slug = ref[len(BITCHUTE_PREFIX):]
            return FollowedChannel(
                ref=ref, channel_id=slug, title=slug, platform="bitchute"
            )
        odysee_id = None
        if ref.startswith(ODYSEE_PREFIX):
            odysee_id = ref[len(ODYSEE_PREFIX):]
        elif "odysee.com/" in ref:
            m = _ODYSEE_CHANNEL_URL_RE.search(ref)
            if not m:
                raise ValueError(f"Could not parse Odysee channel URL {ref!r}")
            odysee_id = m.group(1)
        if odysee_id:
            if not odysee_id.startswith("@"):
                raise ValueError(
                    f"Invalid Odysee channel {odysee_id!r} (expected @name or @name:claim)"
                )
            if ":" in odysee_id:
                title = odysee_id.rsplit(":", 1)[0]
            else:
                # Bare handle ('odysee:@name'): resolve the claim_id via the SDK.
                try:
                    odysee_id, title = await odysee.resolve_channel(odysee_id, client)
                except odysee.OdyseeError as exc:
                    raise ValueError(str(exc)) from exc
            return FollowedChannel(
                ref=f"{ODYSEE_PREFIX}{odysee_id}",
                channel_id=odysee_id,
                title=title,
                platform="odysee",
            )
        tiktok_user = tiktok.parse_username(ref)
        if tiktok_user is not None:
            try:
                data = await tiktok.fetch_user_room(client, tiktok_user)
            except tiktok.TikTokError as exc:
                # Unknown user (or unusable payload) → 404 like other bad refs.
                raise ValueError(str(exc)) from exc
            nickname = (data.get("user") or {}).get("nickname") or ""
            return FollowedChannel(
                ref=f"{TIKTOK_PREFIX}{tiktok_user}",
                channel_id=tiktok_user,
                title=nickname or f"@{tiktok_user}",
                platform="tiktok",
            )
        twitch_login = twitch.parse_login(ref)
        if twitch_login is not None:
            names = await twitch.resolve_display_names(client, [twitch_login])
            if twitch_login not in names:
                raise ValueError(f"Unknown Twitch channel {twitch_login!r}")
            return FollowedChannel(
                ref=f"{TWITCH_PREFIX}{twitch_login}",
                channel_id=twitch_login,
                title=names[twitch_login],
                platform="twitch",
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
        if resp.status_code == 404:
            # The one status that proves the handle doesn't exist.
            channel_id = None
        else:
            # 403/429/5xx are upstream trouble, not "unknown handle":
            # raise → 502 rather than guessing a Twitch fallback.
            resp.raise_for_status()
            channel_id = extract_channel_id(resp.text)
        if channel_id:
            self.db.set_handle(handle, channel_id)
            title = self.db.get_channel_name(channel_id) or ""
            return FollowedChannel(ref=handle, channel_id=channel_id, title=title)
        # Bare refs ("Joueur_du_Grenier") fall back to a Twitch login lookup:
        # YouTube said no, and the name may well be a Twitch channel typed
        # without the twitch: prefix. Explicit "@handle" refs state YouTube
        # intent and never fall back.
        login = ref.lower()
        if not ref.startswith("@") and twitch.LOGIN_RE.fullmatch(login):
            try:
                names = await twitch.resolve_display_names(client, [login])
            except (httpx.HTTPError, twitch.TwitchError) as exc:
                log.debug("twitch fallback lookup failed for %r: %s", login, exc)
                names = {}
            if login in names:
                return FollowedChannel(
                    ref=f"{TWITCH_PREFIX}{login}",
                    channel_id=login,
                    title=names[login],
                    platform="twitch",
                )
            raise ValueError(
                f"Could not resolve {ref!r}: no YouTube handle {handle!r} "
                f"and no Twitch channel {login!r}"
            )
        raise ValueError(f"Could not resolve handle {handle!r} to a channel ID")

    async def _fetch_channel(
        self,
        channel: FollowedChannel,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        force_refresh: bool,
    ) -> tuple[list[Video], str | None]:
        """Fetch one channel's feed. Returns (videos, warning)."""
        async with semaphore:
            if channel.platform == "twitch":
                # No RSS/VOD feed in the merged home feed; Twitch lives
                # surface through /api/lives instead.
                return [], None
            if channel.platform == "tiktok":
                # Live-only platform: lives surface through /api/lives, no
                # video feed.
                return [], None
            if channel.platform == "bitchute":
                return await self._fetch_bitchute(channel, client, force_refresh)
            if channel.platform == "odysee":
                return await self._fetch_odysee(channel, client, force_refresh)
            channel_id = channel.channel_id
            if not force_refresh:
                cached = self.db.get_feed(channel_id)
                if cached is not None:
                    return cached, None
            try:
                resp = await _get_with_retry(
                    client,
                    RSS_URL.format(channel_id=channel_id),
                    headers={"User-Agent": USER_AGENT},
                )
                videos = parse_rss(resp.content)
                self.db.set_feed(channel_id, videos)
                return videos, None
            except Exception as exc:
                log.warning("feed fetch failed for %s: %s", channel.ref, exc)
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
            resp = await _get_with_retry(
                client,
                BITCHUTE_RSS_URL.format(channel_id=channel.channel_id),
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
            videos = parse_bitchute_rss(resp.content, channel_id=channel.channel_id)
            self.db.set_feed(cache_key, videos)
            return videos, None
        except Exception as exc:
            log.warning("feed fetch failed for %s: %s", channel.ref, exc)
            stale = self.db.get_feed(cache_key, allow_stale=True)
            if stale is not None:
                return stale, f"{channel.ref}: using cached data ({type(exc).__name__})"
            return [], f"{channel.ref}: {exc}"

    async def _fetch_odysee(
        self, channel: FollowedChannel, client: httpx.AsyncClient, force_refresh: bool
    ) -> tuple[list[Video], str | None]:
        cache_key = f"{ODYSEE_PREFIX}{channel.channel_id}"
        if not force_refresh:
            cached = self.db.get_feed(cache_key)
            if cached is not None:
                return cached, None
        try:
            resp = await _get_with_retry(
                client,
                ODYSEE_RSS_URL.format(channel_id=channel.channel_id),
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
            videos = parse_odysee_rss(resp.content, channel_id=channel.channel_id)
            self.db.set_feed(cache_key, videos)
            return videos, None
        except Exception as exc:
            log.warning("feed fetch failed for %s: %s", channel.ref, exc)
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
