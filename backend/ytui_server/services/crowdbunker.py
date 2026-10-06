"""CrowdBunker service: channel listings, searches and video details via the
public JSON API.

The platform exposes an undocumented but stable API (api.crowdbunker.com),
without authentication. Video playback does not go through this module:
yt-dlp ships a CrowdBunkerIE for https://crowdbunker.com/v/{uid}, so
streams reuse the regular ytdlp path (with its API host fixed by
patch_ytdlp_extractors). Details are read here: yt-dlp takes the uploader
from the post's `channel`, which the API now leaves empty (the creator lives
under `organization`), so its details carried no channel.

Video identity: video_id = post uid (e.g. "0z4Kms8pi8I"),
channel_id = organization uid (the @handle, e.g. "PruneDePrune").
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..models import Video, VideoDetails

# 2026-09: the API moved from api.divulg.org (now answers 403) to this host;
# the video CDN stayed on the *.divulg.org domains.
API_BASE = "https://api.crowdbunker.com"
LEGACY_API_BASE = "https://api.divulg.org"
TIMEOUT = httpx.Timeout(10.0)
HEADERS = {"accept": "application/json, text/plain, */*"}
# The posts API returns at most 30 items per page.
PAGE_SIZE = 30
# Hits per page asked from the global search API (`perPage`, capped at 50
# by the server; its own default is 15).
SEARCH_PAGE_SIZE = 50
# Safety cutoff: never scan more hits than this for one query.
SEARCH_MAX_HITS = 300
# Both search endpoints answer 400 to queries shorter than this.
MIN_SEARCH_LEN = 2
# The in-channel search pages hold 24 mixed-type posts; never walk more
# pages than this for one query.
CHANNEL_SEARCH_MAX_PAGES = 20
# Most viewed listing: no endpoint sorts a channel by views, so the newest
# videos (up to this many) are ranked locally, together with the channel
# page's "most viewed" shelf (its all-time top 10). The ranking is cached so
# "load more" pages slice one consistent order instead of re-walking.
POPULAR_SCAN_CAP = 200
POPULAR_TTL_SECONDS = 15 * 60
_POPULAR_CACHE_MAX = 64
_popular_cache: dict[str, tuple[float, list[Video]]] = {}

log = logging.getLogger(__name__)


class CrowdBunkerError(Exception):
    """A CrowdBunker upstream call failed."""


class CrowdBunkerNotFound(CrowdBunkerError):
    """The API answered 404: unknown channel or post."""


def _int(value: Any) -> int | None:
    """An int from the API's ints or numeric strings (the search index
    serializes some counters as strings), else None."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def patch_ytdlp_extractors() -> None:
    """Point yt-dlp's CrowdBunker extractors at the current API host.

    yt-dlp (up to 2026.8.19 at least) still fetches /post/{uid}/details from
    the legacy host, which now answers 403, so every CrowdBunker playback
    failed. The JSON downloads are rewritten to API_BASE; once upstream
    follows the move the legacy prefix no longer matches and this is a no-op.
    Idempotent; patches the real classes, which lazy extractors resolve to.
    """
    from yt_dlp.extractor.crowdbunker import CrowdBunkerChannelIE, CrowdBunkerIE

    for cls in (CrowdBunkerIE, CrowdBunkerChannelIE):
        if cls.__dict__.get("_ytui_api_patched"):
            continue
        original = cls._download_json

        def _download_json(self, url_or_request, *args, _original=original, **kwargs):
            if isinstance(url_or_request, str) and url_or_request.startswith(
                LEGACY_API_BASE + "/"
            ):
                url_or_request = API_BASE + url_or_request[len(LEGACY_API_BASE) :]
            return _original(self, url_or_request, *args, **kwargs)

        cls._download_json = _download_json
        setattr(cls, "_ytui_api_patched", True)


def _post_to_video(item: dict[str, Any], channel_id: str) -> Video | None:
    """Map a post item to a Video, or None for posts without a video.

    Channels interleave text posts and censored entries; only items carrying a
    playable video object belong in listings and feeds.
    """
    video = item.get("video") or {}
    uid = item.get("uid") or video.get("id") or ""
    title = video.get("title") or ""
    if not uid or not title or video.get("censored"):
        return None
    published = None
    for stamp in (item.get("publishedAt"), item.get("createdAt")):
        if not stamp:
            continue
        try:
            published = datetime.fromisoformat(stamp)
            break
        except ValueError:
            continue
    thumb = ""
    for image in video.get("thumbnails") or []:
        if (image.get("url") or "").startswith("http"):
            thumb = image["url"]
            break
    return Video(
        video_id=uid,
        title=title,
        channel_title=(item.get("organization") or {}).get("name", ""),
        channel_id=channel_id,
        published=published,
        duration=int(video["duration"]) if video.get("duration") else None,
        thumbnail_url=thumb,
        view_count=_int(video.get("viewCount")),
        kind="video",
        platform="crowdbunker",
    )


def posts_to_videos(items: list[dict], channel_id: str) -> list[Video]:
    """Map a page of post items to videos, dropping non-video posts."""
    return [
        v
        for v in (_post_to_video(item, channel_id) for item in items)
        if v is not None
    ]


async def fetch_posts_page(
    client: httpx.AsyncClient, channel_id: str, after: str | None = None
) -> tuple[list[dict], str | None]:
    """One page of a channel's posts, newest first. Returns (items, next cursor)."""
    params: dict[str, Any] = {}
    if after:
        params["after"] = after
    try:
        resp = await client.get(
            f"{API_BASE}/organization/{channel_id}/posts",
            params=params,
            headers=HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise CrowdBunkerNotFound(f"Unknown CrowdBunker channel {channel_id!r}") from exc
        raise CrowdBunkerError(f"posts fetch failed: {exc}") from exc
    except Exception as exc:
        raise CrowdBunkerError(f"posts fetch failed: {exc}") from exc
    items = data.get("items") or []
    return items, data.get("last") or None


async def channel_videos(channel_id: str, limit: int = 50, offset: int = 0) -> list[Video]:
    """Videos of a channel, newest first.

    The API is cursor-paginated and interleaves non-video posts, so raw post
    indices drift from video indices. `offset` counts *videos*: pages are
    walked from the first and only kept videos are counted, which keeps the
    window exact across 'load more' calls (same approach as Odysee).
    """
    videos: list[Video] = []
    after: str | None = None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            while len(videos) < offset + limit:
                items, after = await fetch_posts_page(client, channel_id, after)
                for item in items:
                    video = _post_to_video(item, channel_id)
                    if video:
                        videos.append(video)
                if after is None:
                    break
    except CrowdBunkerError:
        raise
    except Exception as exc:
        log.warning("crowdbunker channel listing failed for %s: %s", channel_id, exc)
        raise CrowdBunkerError(f"channel listing failed: {exc}") from exc
    return videos[offset : offset + limit]


def _search_hit_to_video(document: dict[str, Any]) -> Video | None:
    """Map a search-index post document to a Video, or None for other media.

    The search endpoints and the channel page shelves share this flat post
    shape; they mix channels, text posts and videos, and only `video`
    entries are playable through the regular ytdlp path.
    """
    if document.get("mediaType") != "video":
        return None
    uid = document.get("uid") or ""
    title = document.get("title") or ""
    if not uid or not title:
        return None
    published = None
    stamp = _int(document.get("publishedAtTimestamp"))
    if stamp:
        try:
            published = datetime.fromtimestamp(stamp, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            published = None
    return Video(
        video_id=uid,
        title=title,
        channel_title=document.get("channelName") or "",
        channel_id=document.get("channelUid") or "",
        published=published,
        duration=_int(document.get("videoDuration")) or None,
        thumbnail_url=document.get("thumbnailUrl") or "",
        view_count=_int(document.get("viewCount")),
        kind="video",
        platform="crowdbunker",
    )


def _search_hit_to_channel(document: dict[str, Any]) -> Video | None:
    """Map a `channel` search hit to a channel item (opens the channel page)."""
    if document.get("mediaType") != "channel":
        return None
    uid = document.get("uid") or document.get("channelUid") or ""
    if not uid:
        return None
    name = document.get("name") or document.get("channelName") or uid
    return Video(
        video_id=uid,
        title=name,
        channel_title=name,
        channel_id=uid,
        thumbnail_url=document.get("thumbnailUrl")
        or document.get("channelThumbnailUrl")
        or "",
        kind="channel",
        platform="crowdbunker",
    )


async def search(query: str, limit: int = 20) -> list[Video]:
    """Global search through the public posts search endpoint (no auth).

    Typesense-shaped response: pages of mixed-type hits (each with the post
    under `document`) are walked until `limit` items are collected, the last
    page is reached, or the server reports `search_cutoff`. Videos and
    channels are kept, in the server's relevance order (a creator's name
    finds their channel); text posts are dropped.
    """
    query = query.strip()
    if len(query) < MIN_SEARCH_LEN:
        return []  # the API answers 400 under two characters
    items: list[Video] = []
    scanned = 0
    page = 1
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        while len(items) < limit and scanned < SEARCH_MAX_HITS:
            try:
                resp = await client.get(
                    f"{API_BASE}/search/posts",
                    params={"q": query, "page": page, "perPage": SEARCH_PAGE_SIZE},
                    headers=HEADERS,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                raise CrowdBunkerError(f"search failed: {exc}") from exc
            hits = data.get("hits") or []
            scanned += len(hits)
            for hit in hits:
                document = hit.get("document") or {}
                item = _search_hit_to_video(document) or _search_hit_to_channel(document)
                if item:
                    items.append(item)
            if len(hits) < SEARCH_PAGE_SIZE or data.get("search_cutoff"):
                break
            page += 1
    return items[:limit]


async def channel_search(
    channel_id: str, query: str, limit: int = 50, offset: int = 0
) -> list[Video]:
    """Videos of one channel matching `query`, in the server's relevance order.

    Uses the channel page's own search, which covers the whole channel (a
    local title scan only sees its newest uploads). Pages hold 24 mixed-type
    posts, so `offset` counts *videos* and pages are walked from the first,
    as in channel_videos. Callers keep queries of MIN_SEARCH_LEN or more.
    """
    videos: list[Video] = []
    page = 1
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        while len(videos) < offset + limit and page <= CHANNEL_SEARCH_MAX_PAGES:
            try:
                resp = await client.get(
                    f"{API_BASE}/organization/{channel_id}/search",
                    params={"q": query.strip(), "page": page},
                    headers=HEADERS,
                )
                if resp.status_code == 404:
                    raise CrowdBunkerNotFound(f"Unknown CrowdBunker channel {channel_id!r}")
                resp.raise_for_status()
                data = resp.json()
            except CrowdBunkerError:
                raise
            except Exception as exc:
                raise CrowdBunkerError(f"channel search failed: {exc}") from exc
            posts = data.get("posts") or []
            for post in posts:
                video = _search_hit_to_video(post)
                if video:
                    videos.append(video)
            if not posts or not data.get("hasMore"):
                break
            page += 1
    return videos[offset : offset + limit]


async def _most_viewed_shelf(channel_id: str) -> list[Video]:
    """The channel page's "most viewed" shelf (all-time top 10), best effort."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                f"{API_BASE}/organization/{channel_id}/sections", headers=HEADERS
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("crowdbunker sections fetch failed for %s: %s", channel_id, exc)
        return []
    for section in data.get("sections") or []:
        if section.get("id") == "most-viewed":
            return [
                v
                for v in (_search_hit_to_video(p) for p in section.get("posts") or [])
                if v is not None
            ]
    return []


async def _rank_by_views(channel_id: str) -> list[Video]:
    recent, top = await asyncio.gather(
        channel_videos(channel_id, limit=POPULAR_SCAN_CAP), _most_viewed_shelf(channel_id)
    )
    merged = {v.video_id: v for v in recent}
    for video in top:
        merged.setdefault(video.video_id, video)
    # Stable sort: equal (or unknown) counts keep the newest first.
    return sorted(merged.values(), key=lambda v: v.view_count or 0, reverse=True)


async def popular_videos(channel_id: str, limit: int = 50, offset: int = 0) -> list[Video]:
    """Videos of a channel, most viewed first.

    Exact for the all-time top 10 (the channel shelf); beyond it, the ranking
    covers the POPULAR_SCAN_CAP newest videos. Cached per channel for
    POPULAR_TTL_SECONDS so successive pages share one order.
    """
    key = channel_id.casefold()  # handles are case-insensitive
    now = time.monotonic()
    cached = _popular_cache.get(key)
    if cached is not None and now - cached[0] < POPULAR_TTL_SECONDS:
        ranked = cached[1]
    else:
        ranked = await _rank_by_views(channel_id)
        if key not in _popular_cache and len(_popular_cache) >= _POPULAR_CACHE_MAX:
            del _popular_cache[min(_popular_cache, key=lambda k: _popular_cache[k][0])]
        _popular_cache[key] = (now, ranked)
    return ranked[offset : offset + limit]


def _pick_thumbnail(thumbnails: list[dict]) -> str:
    """A mid-size thumbnail for the details poster (listings keep the small one)."""
    urls = {
        image.get("name"): image["url"]
        for image in thumbnails
        if (image.get("url") or "").startswith("http")
    }
    for name in ("high", "standard", "medium", "maxres", "default"):
        if urls.get(name):
            return urls[name]
    return next(iter(urls.values()), "")


async def video_details(video_id: str) -> VideoDetails:
    """Details of a video post, with its creator (organization) as channel."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(f"{API_BASE}/post/{video_id}/details", headers=HEADERS)
            if resp.status_code == 404:
                raise CrowdBunkerNotFound(f"Unknown CrowdBunker video {video_id!r}")
            resp.raise_for_status()
            data = resp.json()
    except CrowdBunkerError:
        raise
    except Exception as exc:
        raise CrowdBunkerError(f"details fetch failed: {exc}") from exc
    video = data.get("video") or {}
    if not isinstance(video, dict) or not video.get("title"):
        raise CrowdBunkerError(f"CrowdBunker post {video_id!r} carries no video")
    channel = data.get("channel") if isinstance(data.get("channel"), dict) else {}
    organization = data.get("organization") or channel.get("organization") or {}
    upload_date = ""
    for stamp in (video.get("publishedAt"), data.get("publishedAt"), video.get("createdAt")):
        if not stamp:
            continue
        try:
            upload_date = datetime.fromisoformat(stamp).strftime("%Y-%m-%d")
            break
        except ValueError:
            continue
    return VideoDetails(
        video_id=data.get("uid") or video_id,
        title=video["title"],
        channel_id=organization.get("uid") or "",
        channel_title=organization.get("name")
        or channel.get("name")
        or data.get("authorName")
        or "",
        description=video.get("description") or data.get("textContent") or "",
        view_count=_int(video.get("viewCount")),
        duration=_int(video.get("duration")) or None,
        upload_date=upload_date,
        thumbnail_url=_pick_thumbnail(video.get("thumbnails") or []),
    )


async def resolve_channel(
    handle: str, client: httpx.AsyncClient | None = None
) -> tuple[str, str]:
    """Resolve a CrowdBunker handle (with or without @) to (channel_id, title).

    Probes the posts endpoint: 404 proves the channel does not exist, and the
    first page carries the organization uid and display name.
    """
    uid = handle.lstrip("@")
    if not uid:
        raise CrowdBunkerError("Empty CrowdBunker channel reference")
    owns_client = client is None
    try:
        if client is None:
            client = httpx.AsyncClient(timeout=TIMEOUT)
        try:
            items, _ = await fetch_posts_page(client, uid)
        finally:
            if owns_client:
                await client.aclose()
    except CrowdBunkerNotFound as exc:
        raise CrowdBunkerNotFound(f"Unknown CrowdBunker channel {handle!r}") from exc
    for item in items:
        organization = item.get("organization") or {}
        if organization.get("uid"):
            return organization["uid"], organization.get("name") or organization["uid"]
    # The channel exists but has no posts yet: the uid we probed is canonical.
    return uid, uid
