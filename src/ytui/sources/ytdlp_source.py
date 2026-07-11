"""Search & flat listing via the yt-dlp Python library (blocking; run in a worker thread)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ..models import Video

_YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,
    "skip_download": True,
}

_PLAYLIST_URL_RE = re.compile(r"[?&]list=([\w-]+)")
_CHANNEL_URL_RE = re.compile(r"youtube\.com/(?:channel/|c/|user/|@)")


def entry_to_item(entry: dict) -> Video | None:
    """Convert a flat yt-dlp entry (video, playlist or channel) into a Video item."""
    entry_id = entry.get("id")
    if not entry_id:
        return None
    url = entry.get("url") or ""
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
    if kind == "video":
        thumb = f"https://i.ytimg.com/vi/{entry_id}/mqdefault.jpg"
    else:
        thumbs = entry.get("thumbnails") or []
        if thumbs:
            thumb = thumbs[-1].get("url") or ""
    return Video(
        video_id=entry_id,
        title=entry.get("title") or "",
        channel_title=entry.get("channel") or entry.get("uploader") or "",
        channel_id=entry.get("channel_id") or "",
        published=published,
        duration=int(duration) if duration else None,
        thumbnail_url=thumb,
        kind=kind,
    )


def _extract_items(url: str, limit: int | None = None) -> list[Video]:
    import yt_dlp

    opts = dict(_YDL_OPTS)
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


def search_videos(query: str, limit: int = 20) -> list[Video]:
    """Blocking search returning mixed results (videos, playlists, channels)."""
    from urllib.parse import quote_plus

    return _extract_items(
        f"https://www.youtube.com/results?search_query={quote_plus(query)}", limit=limit
    )


def channel_videos(channel_url: str, limit: int = 50) -> list[Video]:
    """Blocking flat listing of a channel's latest videos."""
    url = channel_url.rstrip("/")
    if not url.endswith("/videos"):
        url += "/videos"
    return _extract_items(url, limit=limit)


def playlist_videos(playlist_url: str, limit: int = 200) -> list[Video]:
    """Blocking flat listing of a playlist's videos."""
    return _extract_items(playlist_url, limit=limit)


class VideoDetails:
    """Full (non-flat) metadata for one video."""

    def __init__(
        self,
        description: str = "",
        view_count: int | None = None,
        like_count: int | None = None,
        duration: int | None = None,
        upload_date: str = "",
        channel_id: str = "",
        channel_title: str = "",
        title: str = "",
    ) -> None:
        self.description = description
        self.view_count = view_count
        self.like_count = like_count
        self.duration = duration
        self.upload_date = upload_date
        self.channel_id = channel_id
        self.channel_title = channel_title
        self.title = title


def video_details(url: str) -> VideoDetails:
    """Blocking non-flat metadata fetch for the VideoDetail screen."""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    upload_date = info.get("upload_date") or ""
    if len(upload_date) == 8:
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    duration = info.get("duration")
    return VideoDetails(
        description=info.get("description") or "",
        view_count=info.get("view_count"),
        like_count=info.get("like_count"),
        duration=int(duration) if duration else None,
        upload_date=upload_date,
        channel_id=info.get("channel_id") or "",
        channel_title=info.get("channel") or info.get("uploader") or "",
        title=info.get("title") or "",
    )
