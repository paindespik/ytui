"""Search via the yt-dlp Python library (blocking; run in a worker thread)."""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Video

_YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,
    "skip_download": True,
}


def _entry_to_video(entry: dict) -> Video | None:
    video_id = entry.get("id")
    if not video_id:
        return None
    published = None
    ts = entry.get("timestamp") or entry.get("release_timestamp")
    if ts:
        published = datetime.fromtimestamp(ts, tz=timezone.utc)
    duration = entry.get("duration")
    return Video(
        video_id=video_id,
        title=entry.get("title") or "",
        channel_title=entry.get("channel") or entry.get("uploader") or "",
        channel_id=entry.get("channel_id") or "",
        published=published,
        duration=int(duration) if duration else None,
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
    )


def search_videos(query: str, limit: int = 20) -> list[Video]:
    """Blocking search using yt-dlp's ytsearchN: pseudo-URL."""
    import yt_dlp

    with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    videos: list[Video] = []
    for entry in info.get("entries") or []:
        video = _entry_to_video(entry)
        if video:
            videos.append(video)
    return videos
