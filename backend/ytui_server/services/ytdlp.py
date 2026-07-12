"""yt-dlp based search, listings, details and stream URL resolution.

All yt-dlp calls are blocking: they are wrapped with anyio.to_thread and a
global semaphore to bound server load.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote_plus, urlparse

import anyio

from ..models import StreamInfo, Video, VideoDetails

_YDL_FLAT_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,
    "skip_download": True,
}

_PLAYLIST_URL_RE = re.compile(r"[?&]list=([\w-]+)")
_CHANNEL_URL_RE = re.compile(r"youtube\.com/(?:channel/|c/|user/|@)")

_SEMAPHORE = asyncio.Semaphore(4)


class UpstreamError(Exception):
    """yt-dlp extraction failed."""


def entry_to_item(entry: dict) -> Video | None:
    """Convert a flat yt-dlp entry (video, playlist or channel) into a Video item."""
    entry_id = entry.get("id")
    if not entry_id:
        return None
    url = entry.get("url") or entry.get("webpage_url") or ""
    platform = "bitchute" if "bitchute.com" in url else "youtube"
    kind = "video"
    if entry.get("ie_key") == "YoutubeTab" or entry.get("_type") == "playlist":
        if _PLAYLIST_URL_RE.search(url) or entry_id.startswith(("PL", "RD", "OL", "UU", "LL")):
            kind = "playlist"
        elif _CHANNEL_URL_RE.search(url) or entry_id.startswith("UC"):
            kind = "channel"
        else:
            kind = "playlist"
    published = None
    ts = entry.get("timestamp") or entry.get("release_timestamp")
    if ts:
        published = datetime.fromtimestamp(ts, tz=timezone.utc)
    duration = entry.get("duration")
    thumb = ""
    if kind == "video" and platform == "youtube":
        thumb = f"https://i.ytimg.com/vi/{entry_id}/mqdefault.jpg"
    else:
        thumbs = entry.get("thumbnails") or []
        if thumbs:
            thumb = thumbs[-1].get("url") or ""
        thumb = thumb or entry.get("thumbnail") or ""
    return Video(
        video_id=entry_id,
        title=entry.get("title") or "",
        channel_title=entry.get("channel") or entry.get("uploader") or "",
        channel_id=entry.get("channel_id") or "",
        published=published,
        duration=int(duration) if duration else None,
        thumbnail_url=thumb,
        kind=kind,
        platform=platform,
    )


def _extract_flat(url: str, limit: int | None = None) -> list[Video]:
    import yt_dlp

    opts = dict(_YDL_FLAT_OPTS)
    if limit:
        opts["playlist_items"] = f"1:{limit}"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    items: list[Video] = []
    for entry in info.get("entries") or []:
        item = entry_to_item(entry)
        if item:
            items.append(item)
    return items


def _extract_flat_info(url: str, limit: int | None = None) -> tuple[list[Video], str]:
    """Like _extract_flat but also returns the container title (playlist name)."""
    import yt_dlp

    opts = dict(_YDL_FLAT_OPTS)
    if limit:
        opts["playlist_items"] = f"1:{limit}"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    items: list[Video] = []
    for entry in info.get("entries") or []:
        item = entry_to_item(entry)
        if item:
            items.append(item)
    return items, info.get("title") or ""


async def _run(func, *args):
    async with _SEMAPHORE:
        try:
            return await anyio.to_thread.run_sync(func, *args)
        except Exception as exc:
            raise UpstreamError(str(exc)) from exc


async def search_videos(query: str, limit: int = 20) -> list[Video]:
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    return await _run(_extract_flat, url, limit)


async def channel_videos(channel_url: str, limit: int = 50) -> list[Video]:
    url = channel_url.rstrip("/")
    if "bitchute.com" not in url and not url.endswith("/videos"):
        url += "/videos"
    return await _run(_extract_flat, url, limit)


async def playlist_videos(playlist_url: str, limit: int = 200) -> tuple[list[Video], str]:
    return await _run(_extract_flat_info, playlist_url, limit)


def _extract_details(url: str) -> VideoDetails:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    upload_date = info.get("upload_date") or ""
    if len(upload_date) == 8:
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    duration = info.get("duration")
    return VideoDetails(
        video_id=info.get("id") or "",
        title=info.get("title") or "",
        channel_id=info.get("channel_id") or "",
        channel_title=info.get("channel") or info.get("uploader") or "",
        description=info.get("description") or "",
        view_count=info.get("view_count"),
        like_count=info.get("like_count"),
        duration=int(duration) if duration else None,
        upload_date=upload_date,
    )


async def video_details(url: str) -> VideoDetails:
    return await _run(_extract_details, url)


def _stream_expiry(url: str) -> datetime | None:
    """googlevideo URLs carry an 'expire' unix timestamp query param."""
    try:
        qs = parse_qs(urlparse(url).query)
        expire = qs.get("expire", [None])[0]
        if expire:
            return datetime.fromtimestamp(int(expire), tz=timezone.utc)
    except (ValueError, TypeError):
        pass
    return None


def _extract_streams(url: str, max_height: int, audio_only: bool) -> StreamInfo:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    title = info.get("title") or ""
    duration = info.get("duration")
    duration = int(duration) if duration else None
    formats = info.get("formats") or []

    def make(kind, url_, video_url=None, audio_url=None):
        return StreamInfo(
            kind=kind,
            url=url_,
            video_url=video_url,
            audio_url=audio_url,
            title=title,
            duration=duration,
            expires_at=_stream_expiry(url_),
        )

    if audio_only:
        audio = [
            f
            for f in formats
            if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
        ]
        if audio:
            best = max(audio, key=lambda f: f.get("abr") or f.get("tbr") or 0)
            return make("progressive", best["url"])
        # fall through: use whatever combined format exists

    height_ok = lambda f: (f.get("height") or 0) <= max_height  # noqa: E731

    # 1. HLS manifest (adaptive; best for mobile players)
    hls = [
        f
        for f in formats
        if f.get("protocol", "").startswith("m3u8")
        and f.get("vcodec") not in (None, "none")
        and height_ok(f)
    ]
    if hls:
        manifest = info.get("manifest_url") or ""
        best = max(hls, key=lambda f: f.get("height") or 0)
        return make("hls", manifest or best["url"])

    # 2. Progressive (audio+video in one file)
    progressive = [
        f
        for f in formats
        if f.get("acodec") not in (None, "none")
        and f.get("vcodec") not in (None, "none")
        and height_ok(f)
    ]
    if progressive:
        best = max(progressive, key=lambda f: f.get("height") or 0)
        return make("progressive", best["url"])

    # 3. Split DASH: best video + best audio
    videos = [
        f for f in formats if f.get("vcodec") not in (None, "none") and height_ok(f)
    ]
    audios = [
        f
        for f in formats
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    if videos and audios:
        bv = max(videos, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
        ba = max(audios, key=lambda f: f.get("abr") or f.get("tbr") or 0)
        return make("split", bv["url"], video_url=bv["url"], audio_url=ba["url"])

    # 4. Last resort: top-level url
    if info.get("url"):
        return make("progressive", info["url"])
    raise UpstreamError("No playable stream found")


async def resolve_streams(
    url: str, max_height: int = 1080, audio_only: bool = False
) -> StreamInfo:
    return await _run(_extract_streams, url, max_height, audio_only)
