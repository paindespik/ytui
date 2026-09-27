"""BitChute service: channel listings via the public JSON API.

yt-dlp's BitChuteChannelIE scrapes old.bitchute.com and yields entries
without thumbnail or upload date, so channel pages showed blank cards. The
API behind the current bitchute.com SPA (api.bitchute.com/api/beta, no auth)
returns both. Playback and details still go through yt-dlp (BitChuteIE).

Channel identity: the API accepts both the channel slug ("itv_john_doe",
what `bitchute:` refs store) and the hash id yt-dlp reports as channel_id
("Ynl2NN4t7THl", used by the related-videos fallback).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from ..models import Video
from . import ytdlp

API_BASE = "https://api.bitchute.com/api/beta"
TIMEOUT = httpx.Timeout(10.0)
HEADERS = {"accept": "application/json"}
# The channel videos endpoint silently caps every page at 50 items.
PAGE_SIZE = 50

log = logging.getLogger(__name__)


class BitChuteError(Exception):
    """A BitChute API call failed."""


def _parse_duration(text: Any) -> int | None:
    """Seconds from a "SS", "M:SS" or "H:MM:SS" string, or None."""
    if not isinstance(text, str) or not text.strip():
        return None
    total = 0
    try:
        for part in text.strip().split(":"):
            total = total * 60 + int(part)
    except ValueError:
        return None
    return total or None


def _parse_date(stamp: Any) -> datetime | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _item_to_video(item: dict[str, Any], channel_id: str) -> Video | None:
    video_id = item.get("video_id") or ""
    if not video_id or item.get("state_id", "published") != "published":
        return None
    view_count = item.get("view_count")
    return Video(
        video_id=video_id,
        title=item.get("video_name") or "",
        channel_id=channel_id,
        published=_parse_date(item.get("date_published")),
        duration=_parse_duration(item.get("duration")),
        thumbnail_url=item.get("thumbnail_url") or "",
        view_count=view_count if isinstance(view_count, int) else None,
        kind="video",
        platform="bitchute",
    )


async def _fetch_page(
    client: httpx.AsyncClient, channel_id: str, offset: int, limit: int
) -> list[dict]:
    try:
        resp = await client.post(
            f"{API_BASE}/channel/videos",
            json={"channel_id": channel_id, "offset": offset, "limit": limit},
            headers=HEADERS,
        )
        resp.raise_for_status()
        return resp.json().get("videos") or []
    except Exception as exc:
        raise BitChuteError(f"channel videos fetch failed: {exc}") from exc


async def _api_channel_videos(channel_id: str, limit: int, offset: int) -> list[Video]:
    """The API is offset-paginated over published videos only, so `offset`
    maps 1:1 to the listing index; pages are walked until `limit` is met."""
    videos: list[Video] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        while len(videos) < limit:
            want = min(PAGE_SIZE, limit - len(videos))
            items = await _fetch_page(client, channel_id, offset, want)
            offset += len(items)
            for item in items:
                video = _item_to_video(item, channel_id)
                if video:
                    videos.append(video)
            if len(items) < want:
                break
    return videos[:limit]


async def channel_videos(channel_id: str, limit: int = 50, offset: int = 0) -> list[Video]:
    """Videos of a channel, newest first, with thumbnails and dates.

    Falls back to yt-dlp's (thumbnail-less) scraper when the API fails, so a
    change on BitChute's side degrades the cards instead of the listing.
    """
    try:
        return await _api_channel_videos(channel_id, limit, offset)
    except BitChuteError as exc:
        log.warning("bitchute API listing failed for %s, using yt-dlp: %s", channel_id, exc)
    url = Video(video_id=channel_id, title="", kind="channel", platform="bitchute").url
    return await ytdlp.channel_videos(url, limit=limit, offset=offset)
