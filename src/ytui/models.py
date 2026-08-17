"""Data models."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

_YT_WATCH_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{6,})")
_YT_SHORT_RE = re.compile(r"youtu\.be/([A-Za-z0-9_-]{6,})")
_BITCHUTE_RE = re.compile(r"bitchute\.com/video/([^/?#]+)")
_CROWDBUNKER_RE = re.compile(r"crowdbunker\.com/v/([^/?#]+)")
_ODYSEE_RE = re.compile(r"odysee\.com/(?:@[^/?#]+/)?([^/?#@][^/?#]*:[0-9a-f]+)")
_TWITCH_VOD_RE = re.compile(r"twitch\.tv/videos/(\d+)")
_TIKTOK_VIDEO_RE = re.compile(r"tiktok\.com/@[^/?#]+/video/(\d+)")


def video_id_from_url(url: str) -> tuple[str, str] | None:
    """(video_id, platform) extracted from a video URL, or None.

    Twitch VODs map to the 'v{id}' convention used by Video.url; TikTok video
    pages map to the bare numeric id.
    """
    match = _YT_WATCH_RE.search(url) or _YT_SHORT_RE.search(url)
    if match:
        return match.group(1), "youtube"
    match = _BITCHUTE_RE.search(url)
    if match:
        return match.group(1), "bitchute"
    match = _CROWDBUNKER_RE.search(url)
    if match:
        return match.group(1), "crowdbunker"
    match = _TWITCH_VOD_RE.search(url)
    if match:
        return f"v{match.group(1)}", "twitch"
    match = _TIKTOK_VIDEO_RE.search(url)
    if match:
        return match.group(1), "tiktok"
    match = _ODYSEE_RE.search(url)
    if match:
        return match.group(1), "odysee"
    return None


class Channel(BaseModel):
    channel_id: str
    title: str = ""
    platform: Literal[
        "youtube", "bitchute", "odysee", "twitch", "tiktok", "crowdbunker"
    ] = "youtube"

    @property
    def rss_url(self) -> str:
        if self.platform == "bitchute":
            return f"https://api.bitchute.com/feeds/rss/channel/{self.channel_id}"
        if self.platform == "odysee":
            return f"https://odysee.com/$/rss/{self.channel_id}"
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
    platform: Literal[
        "youtube", "bitchute", "odysee", "twitch", "tiktok", "crowdbunker"
    ] = "youtube"
    playlist_id: str = ""  # parent YouTube playlist when launched from PlaylistScreen

    @property
    def url(self) -> str:
        if self.platform == "crowdbunker":
            if self.kind == "channel":
                return f"https://crowdbunker.com/@{self.video_id}"
            return f"https://crowdbunker.com/v/{self.video_id}"
        if self.platform == "odysee":
            if self.kind == "playlist":
                return f"https://odysee.com/$/playlist/{self.video_id}"
            return f"https://odysee.com/{self.video_id}"
        if self.platform == "bitchute":
            if self.kind == "channel":
                return f"https://www.bitchute.com/channel/{self.video_id}/"
            if self.kind == "playlist":
                return f"https://www.bitchute.com/playlist/{self.video_id}/"
            return f"https://www.bitchute.com/video/{self.video_id}/"
        if self.platform == "twitch":
            if self.kind == "channel":
                return f"https://www.twitch.tv/{self.channel_id or self.video_id}"
            if ":" in self.video_id:
                # Live id "login:stream_id": the channel page plays the live.
                return f"https://www.twitch.tv/{self.video_id.split(':', 1)[0]}"
            if self.video_id.startswith("v") and self.video_id[1:].isdigit():
                return f"https://www.twitch.tv/videos/{self.video_id[1:]}"
            return f"https://www.twitch.tv/{self.video_id}"
        if self.platform == "tiktok":
            if self.kind == "channel":
                return f"https://www.tiktok.com/@{self.video_id}"
            if ":" in self.video_id:
                # Live id "username:room_id": the /live page plays the stream.
                return f"https://www.tiktok.com/@{self.video_id.split(':', 1)[0]}/live"
            if self.channel_id:
                return f"https://www.tiktok.com/@{self.channel_id}/video/{self.video_id}"
            return f"https://www.tiktok.com/embed/{self.video_id}"
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


class Comment(BaseModel):
    """A single video comment, as served by the backend API (YouTube, Odysee)."""

    comment_id: str
    text: str
    channel_name: str = ""
    timestamp: int | None = None
    replies: int = 0
    likes: int = 0
    dislikes: int = 0
    is_pinned: bool = False


class CommentPage(BaseModel):
    """One page of comments: the items plus the paging/availability metadata."""

    items: list[Comment] = []
    # Only filled in on the first page for YouTube; 0 afterwards.
    total: int = 0
    # Opaque token to pass back as `cursor`; None once the last page was reached.
    next_cursor: str | None = None
    # Comments turned off by the uploader: an empty list is the normal answer.
    disabled: bool = False


class ChatMessage(BaseModel):
    """A single live-chat message, as served by the backend API (read-only)."""

    id: str = ""
    author: str = ""
    text: str = ""
    color: str | None = None
    timestamp: float = 0.0


class VideoDetails(BaseModel):
    """Full metadata for one video, as served by the backend API."""

    video_id: str = ""
    title: str = ""
    channel_id: str = ""
    channel_title: str = ""
    description: str = ""
    view_count: int | None = None
    like_count: int | None = None
    duration: int | None = None
    upload_date: str = ""


class SponsorSegment(BaseModel):
    """A SponsorBlock skip segment, as served by the backend API."""

    category: str
    start: float
    end: float
