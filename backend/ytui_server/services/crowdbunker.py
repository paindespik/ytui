"""CrowdBunker service: channel post listings via the public JSON API.

The platform exposes an undocumented but stable API (api.divulg.org) without
authentication. Video playback does not go through this module: yt-dlp ships
a CrowdBunkerIE for https://crowdbunker.com/v/{uid}, so details/streams reuse
the regular ytdlp path.

Video identity: video_id = post uid (e.g. "0z4Kms8pi8I"),
channel_id = organization uid (the @handle, e.g. "PruneDePrune").
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ..models import Video

API_BASE = "https://api.divulg.org"
TIMEOUT = httpx.Timeout(10.0)
HEADERS = {"accept": "application/json, text/plain, */*"}
# The posts API returns at most 30 items per page.
PAGE_SIZE = 30
# The search API returns at most 15 hits per page, fixed by the server.
SEARCH_PAGE_SIZE = 15
# Safety cutoff: never scan more hits than this for one query.
SEARCH_MAX_HITS = 300

log = logging.getLogger(__name__)


class CrowdBunkerError(Exception):
    """A CrowdBunker upstream call failed."""


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
            raise CrowdBunkerError(f"Unknown CrowdBunker channel {channel_id!r}") from exc
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


def _search_hit_to_video(item: dict[str, Any]) -> Video | None:
    """Map a search hit to a Video, or None for non-video media types.

    Search results mix channels, text posts and videos; only `video` entries
    are playable through the regular ytdlp path.
    """
    if item.get("mediaType") != "video":
        return None
    uid = item.get("uid") or ""
    title = item.get("title") or ""
    if not uid or not title:
        return None
    published = None
    stamp = item.get("publishedAtTimestamp")
    if stamp:
        try:
            published = datetime.fromtimestamp(int(stamp), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            published = None
    duration = item.get("videoDuration") or (item.get("video") or {}).get("duration")
    return Video(
        video_id=uid,
        title=title,
        channel_title=item.get("channelName") or "",
        channel_id=item.get("channelUid") or "",
        published=published,
        duration=int(duration) if duration else None,
        thumbnail_url=item.get("thumbnailUrl") or "",
        kind="video",
        platform="crowdbunker",
    )


async def search(query: str, limit: int = 20) -> list[Video]:
    """Global video search through the public posts search endpoint (no auth).

    Pages of 15 mixed-type hits are walked until `limit` videos are collected
    or the hit cutoff is reached.
    """
    videos: list[Video] = []
    scanned = 0
    page = 1
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            while len(videos) < limit and scanned < SEARCH_MAX_HITS:
                try:
                    resp = await client.get(
                        f"{API_BASE}/search/posts",
                        params={"q": query, "page": page},
                        headers=HEADERS,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    raise CrowdBunkerError(f"search failed: {exc}") from exc
                items = data.get("items") or []
                scanned += len(items)
                for item in items:
                    video = _search_hit_to_video(item)
                    if video:
                        videos.append(video)
                if len(items) < SEARCH_PAGE_SIZE:
                    break
                page += 1
    except CrowdBunkerError:
        raise
    return videos[:limit]


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
    except CrowdBunkerError as exc:
        if "Unknown" in str(exc):
            raise CrowdBunkerError(f"Unknown CrowdBunker channel {handle!r}") from exc
        raise
    for item in items:
        organization = item.get("organization") or {}
        if organization.get("uid"):
            return organization["uid"], organization.get("name") or organization["uid"]
    # The channel exists but has no posts yet: the uid we probed is canonical.
    return uid, uid
