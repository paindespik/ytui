"""Data models."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

_YT_WATCH_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{6,})")
_YT_SHORT_RE = re.compile(r"youtu\.be/([A-Za-z0-9_-]{6,})")
_BITCHUTE_RE = re.compile(r"bitchute\.com/video/([^/?#]+)")


def video_id_from_url(url: str) -> str | None:
    """Extract a video id from a YouTube/BitChute video URL, or None."""
    match = _YT_WATCH_RE.search(url) or _YT_SHORT_RE.search(url) or _BITCHUTE_RE.search(url)
    return match.group(1) if match else None


class Channel(BaseModel):
    channel_id: str
    title: str = ""
    platform: Literal["youtube", "bitchute"] = "youtube"

    @property
    def rss_url(self) -> str:
        if self.platform == "bitchute":
            return f"https://api.bitchute.com/feeds/rss/channel/{self.channel_id}"
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={self.channel_id}"


class Video(BaseModel):
    """A browsable item: a video, a playlist or a channel (see `kind`)."""

    video_id: str
    title: str
    channel_title: str = ""
    channel_id: str = ""
    published: datetime | None = None
    duration: int | None = None  # seconds
    thumbnail_url: str = ""
    kind: Literal["video", "playlist", "channel"] = "video"
    platform: Literal["youtube", "bitchute"] = "youtube"
    playlist_id: str = ""  # parent YouTube playlist when launched from PlaylistScreen

    @property
    def url(self) -> str:
        if self.platform == "bitchute":
            if self.kind == "channel":
                return f"https://www.bitchute.com/channel/{self.video_id}/"
            if self.kind == "playlist":
                return f"https://www.bitchute.com/playlist/{self.video_id}/"
            return f"https://www.bitchute.com/video/{self.video_id}/"
        if self.kind == "playlist":
            return f"https://www.youtube.com/playlist?list={self.video_id}"
        if self.kind == "channel":
            return f"https://www.youtube.com/channel/{self.video_id}"
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def age(self) -> str:
        """Human-readable age like '3h ago' or '2d ago'."""
        if self.published is None:
            return ""
        now = datetime.now(tz=self.published.tzinfo)
        delta = now - self.published
        seconds = int(delta.total_seconds())
        if seconds < 0:
            seconds = 0
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        days = seconds // 86400
        if days < 30:
            return f"{days}d ago"
        if days < 365:
            return f"{days // 30}mo ago"
        return f"{days // 365}y ago"
