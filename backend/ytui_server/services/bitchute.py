"""BitChute service: channel and playlist listings via the public JSON API.

yt-dlp's BitChuteChannelIE/BitChutePlaylistIE scrape old.bitchute.com and
yield entries without thumbnail or upload date, so channel and playlist pages
showed blank cards. The API behind the current bitchute.com SPA
(api.bitchute.com/api/beta, no auth) returns both. Playback and details still
go through yt-dlp (BitChuteIE).

Playlists belong to the channel owner's *profile*, not to the channel: the
channel record gives the profile_id, whose public playlists are listed by
/profile/playlists (fixed 10-item pages, `limit` ignored upstream).

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
# /profile/playlists ignores `limit` and always answers 10 items.
PLAYLISTS_PAGE_SIZE = 10
# /playlist/videos caps its pages like /channel/videos.
PLAYLIST_VIDEOS_PAGE_SIZE = 50

log = logging.getLogger(__name__)


class BitChuteError(Exception):
    """A BitChute API call failed."""


class BitChuteNotFound(BitChuteError):
    """The API answered 404: unknown channel or playlist."""


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


def _item_to_video(
    item: dict[str, Any], channel_id: str, channel_title: str = ""
) -> Video | None:
    video_id = item.get("video_id") or ""
    if not video_id or item.get("state_id", "published") != "published":
        return None
    view_count = item.get("view_count")
    return Video(
        video_id=video_id,
        title=item.get("video_name") or "",
        channel_title=channel_title,
        channel_id=channel_id,
        published=_parse_date(item.get("date_published")),
        duration=_parse_duration(item.get("duration")),
        thumbnail_url=item.get("thumbnail_url") or "",
        view_count=view_count if isinstance(view_count, int) else None,
        kind="video",
        platform="bitchute",
    )


async def _post(client: httpx.AsyncClient, path: str, body: dict[str, Any]) -> dict:
    try:
        resp = await client.post(f"{API_BASE}/{path}", json=body, headers=HEADERS)
        if resp.status_code == 404:
            raise BitChuteNotFound(f"{path}: not found")
        resp.raise_for_status()
        data = resp.json()
    except BitChuteError:
        raise
    except Exception as exc:
        raise BitChuteError(f"{path} fetch failed: {exc}") from exc
    if not isinstance(data, dict):
        raise BitChuteError(f"{path}: unexpected payload")
    return data


# Channel listing orders accepted by /channel/videos (the SPA's Latest /
# Popular / Oldest), keyed by ytui's sort names.
ORDER_BY = {"date": "latest", "views": "popular"}


async def _fetch_page(
    client: httpx.AsyncClient, channel_id: str, offset: int, limit: int, order_by: str
) -> list[dict]:
    try:
        resp = await client.post(
            f"{API_BASE}/channel/videos",
            json={
                "channel_id": channel_id,
                "offset": offset,
                "limit": limit,
                "order_by": order_by,
            },
            headers=HEADERS,
        )
        resp.raise_for_status()
        return resp.json().get("videos") or []
    except Exception as exc:
        raise BitChuteError(f"channel videos fetch failed: {exc}") from exc


async def _api_channel_videos(
    channel_id: str, limit: int, offset: int, order_by: str
) -> list[Video]:
    """The API is offset-paginated over published videos only, so `offset`
    maps 1:1 to the listing index; pages are walked until `limit` is met."""
    videos: list[Video] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        while len(videos) < limit:
            want = min(PAGE_SIZE, limit - len(videos))
            items = await _fetch_page(client, channel_id, offset, want, order_by)
            offset += len(items)
            for item in items:
                video = _item_to_video(item, channel_id)
                if video:
                    videos.append(video)
            if len(items) < want:
                break
    return videos[:limit]


async def channel_videos(
    channel_id: str, limit: int = 50, offset: int = 0, sort: str = "date"
) -> list[Video]:
    """Videos of a channel with thumbnails and dates: newest first, or most
    viewed first with sort="views" (ordered server-side).

    Falls back to yt-dlp's (thumbnail-less) scraper when the API fails, so a
    change on BitChute's side degrades the cards instead of the listing. The
    scraper only lists newest first: a views listing raises BitChuteError
    rather than come back in the wrong order.
    """
    try:
        return await _api_channel_videos(channel_id, limit, offset, ORDER_BY[sort])
    except BitChuteError as exc:
        if sort != "date":
            raise
        log.warning("bitchute API listing failed for %s, using yt-dlp: %s", channel_id, exc)
    url = Video(video_id=channel_id, title="", kind="channel", platform="bitchute").url
    return await ytdlp.channel_videos(url, limit=limit, offset=offset)


def _playlist_to_item(
    item: dict[str, Any], channel_id: str, channel_title: str
) -> Video | None:
    playlist_id = item.get("playlist_id") or ""
    if not playlist_id or item.get("is_public") is False:
        return None
    return Video(
        video_id=playlist_id,
        title=item.get("playlist_name") or "",
        channel_title=channel_title,
        channel_id=channel_id,
        published=_parse_date(item.get("date_created")),
        thumbnail_url=item.get("thumbnail_url") or "",
        kind="playlist",
        platform="bitchute",
    )


async def channel_playlists(channel_id: str, limit: int = 50, offset: int = 0) -> list[Video]:
    """Public playlists of a channel's owner, in BitChute's order.

    `offset` indexes the upstream listing; pages are walked until `limit` is
    met or the reported playlist_count is reached. Raises BitChuteNotFound
    for an unknown channel. No yt-dlp fallback: it has no such listing.
    """
    playlists: list[Video] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        channel = await _post(client, "channel", {"channel_id": channel_id})
        profile_id = channel.get("profile_id") or ""
        if not profile_id:
            return []
        channel_title = channel.get("channel_name") or ""
        while len(playlists) < limit:
            data = await _post(
                client,
                "profile/playlists",
                {"profile_id": profile_id, "offset": offset, "limit": PLAYLISTS_PAGE_SIZE},
            )
            items = data.get("playlists") or []
            offset += len(items)
            for item in items:
                playlist = _playlist_to_item(item, channel_id, channel_title)
                if playlist:
                    playlists.append(playlist)
            total = data.get("playlist_count")
            if not items or (isinstance(total, int) and offset >= total):
                break
    return playlists[:limit]


async def _api_playlist_videos(playlist_id: str, limit: int) -> tuple[list[Video], str]:
    videos: list[Video] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        meta = await _post(client, "playlist", {"playlist_id": playlist_id})
        offset = 0
        while len(videos) < limit:
            want = min(PLAYLIST_VIDEOS_PAGE_SIZE, limit - len(videos))
            data = await _post(
                client,
                "playlist/videos",
                {"playlist_id": playlist_id, "offset": offset, "limit": want},
            )
            entries = data.get("videos") or []
            offset += len(entries)
            for entry in entries:
                item = entry.get("video") if isinstance(entry, dict) else None
                if not isinstance(item, dict):
                    continue
                channel = item.get("channel") or {}
                video = _item_to_video(
                    item, channel.get("channel_id") or "", channel.get("channel_name") or ""
                )
                if video:
                    videos.append(video)
            if len(entries) < want:
                break
    return videos[:limit], meta.get("playlist_name") or ""


async def playlist_videos(playlist_id: str, limit: int = 200) -> tuple[list[Video], str]:
    """Videos of a playlist (with thumbnails, dates, channels) and its title.

    Falls back to yt-dlp's thumbnail-less scraper when the API fails, like
    channel_videos.
    """
    try:
        return await _api_playlist_videos(playlist_id, limit)
    except BitChuteError as exc:
        log.warning("bitchute API playlist failed for %s, using yt-dlp: %s", playlist_id, exc)
    url = Video(video_id=playlist_id, title="", kind="playlist", platform="bitchute").url
    return await ytdlp.playlist_videos(url, limit=limit)
