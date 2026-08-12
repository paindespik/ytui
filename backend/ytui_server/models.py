"""Data models: Video/Channel (mirroring the ytui client) plus API schemas."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, computed_field

_YT_WATCH_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{6,})")
_YT_SHORT_RE = re.compile(r"youtu\.be/([A-Za-z0-9_-]{6,})")
_BITCHUTE_RE = re.compile(r"bitchute\.com/video/([^/?#]+)")
_ODYSEE_RE = re.compile(r"odysee\.com/(?:@[^/?#]+/)?([^/?#@][^/?#]*:[0-9a-f]+)")
_YT_LIST_RE = re.compile(r"[?&]list=([A-Za-z0-9_-]+)")


def video_id_from_url(url: str) -> str | None:
    """Extract a video id from a YouTube/BitChute/Odysee video URL, or None."""
    match = _YT_WATCH_RE.search(url) or _YT_SHORT_RE.search(url) or _BITCHUTE_RE.search(url)
    if match:
        return match.group(1)
    match = _ODYSEE_RE.search(url)
    return match.group(1) if match else None


def playlist_id_from_url(text: str) -> str:
    """Accept a playlist id or any URL carrying one, and return the bare id.

    Clients let users paste a full "…/playlist?list=PL…" (or a watch URL opened
    from a playlist), so the id is extracted server-side once for every surface.
    """
    value = text.strip()
    match = _YT_LIST_RE.search(value)
    if match:
        return match.group(1)
    if "://" in value:
        # Non-YouTube playlist URLs end with the id (BitChute, Odysee…).
        return value.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
    return value


class Channel(BaseModel):
    channel_id: str
    title: str = ""
    platform: Literal["youtube", "bitchute", "odysee", "twitch", "tiktok"] = "youtube"

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
    platform: Literal["youtube", "bitchute", "odysee", "twitch", "tiktok"] = "youtube"
    playlist_id: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
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


# ─── API schemas ───


class FeedResponse(BaseModel):
    videos: list[Video]
    warnings: list[str] = []


class SearchResponse(BaseModel):
    items: list[Video]


class ChannelVideosResponse(BaseModel):
    items: list[Video]
    channel: Channel
    has_more: bool = False


class PlaylistVideosResponse(BaseModel):
    items: list[Video]
    title: str = ""


class VideoDetails(BaseModel):
    video_id: str = ""
    title: str = ""
    channel_id: str = ""
    channel_title: str = ""
    description: str = ""
    view_count: int | None = None
    like_count: int | None = None
    duration: int | None = None
    upload_date: str = ""


class SubtitleTrackOut(BaseModel):
    lang: str
    label: str = ""
    url: str
    auto: bool = False


class StreamInfo(BaseModel):
    kind: Literal["hls", "progressive", "split"]
    url: str
    video_url: str | None = None
    audio_url: str | None = None
    title: str = ""
    duration: int | None = None
    expires_at: datetime | None = None
    # Height of the video track actually served (None: audio-only or unknown).
    height: int | None = None
    subtitles: list[SubtitleTrackOut] = []


class FollowedChannel(BaseModel):
    ref: str
    channel_id: str
    title: str = ""
    platform: Literal["youtube", "bitchute", "odysee", "twitch", "tiktok"] = "youtube"


class ChannelIn(BaseModel):
    ref: str


class HistoryEntry(BaseModel):
    video: Video
    watched_at: float
    position: float = 0.0


class HistoryIn(BaseModel):
    video: Video


class PositionIn(BaseModel):
    position: float
    duration: float | None = None


class ResumeOut(BaseModel):
    position: float
    duration: float | None = None
    playlist_id: str = ""


class WatchedIdsOut(BaseModel):
    ids: list[str]


class PlaylistOut(BaseModel):
    id: int
    name: str
    created_at: float
    count: int


class PlaylistIn(BaseModel):
    name: str


class PlaylistItemOut(BaseModel):
    position: int
    video: Video


class PlaylistItemIn(BaseModel):
    video: Video


class PlaylistImportIn(BaseModel):
    """Import a whole upstream playlist into a local one.

    `source` is a playlist id or any URL carrying one. Without `target_id` a new
    local playlist is created, named after `name` or the upstream title.
    """

    source: str
    platform: Literal["youtube", "bitchute", "odysee", "twitch", "tiktok"] = "youtube"
    name: str = ""
    target_id: int | None = None
    limit: int = 500


class PlaylistImportOut(BaseModel):
    playlist: PlaylistOut
    added: int
    skipped: int
    source_title: str = ""


class CommentIn(BaseModel):
    text: str


class CommentOut(BaseModel):
    comment_id: str
    text: str
    channel_name: str = ""
    timestamp: int | None = None
    replies: int = 0
    likes: int = 0
    dislikes: int = 0
    is_pinned: bool = False


class CommentsResponse(BaseModel):
    items: list[CommentOut]
    total: int = 0
    # Opaque continuation to pass back as `cursor` (YouTube page token, Odysee
    # page number); None once the last page was reached.
    next_cursor: str | None = None
    # Comments turned off by the uploader: an empty list is the normal answer,
    # and posting would fail.
    disabled: bool = False


class RatingOut(BaseModel):
    """The signed-in account's rating for one video: like, dislike or none."""

    rating: str = "none"


class ChatMessage(BaseModel):
    id: str
    author: str
    text: str
    color: str | None = None
    timestamp: float = 0.0


class ChatResponse(BaseModel):
    messages: list[ChatMessage] = []
    cursor: int = 0
    active: bool = True


class LiveOut(BaseModel):
    video: Video
    detected_at: datetime


class SessionIn(BaseModel):
    token: str


class AuthStatusOut(BaseModel):
    authenticated: bool


class YouTubeAuthStatusOut(BaseModel):
    authenticated: bool
    # Account cookies for the personalised home feed; independent of the token.
    cookies: bool = False


class StatusOut(BaseModel):
    version: str
    uptime_seconds: float
    db_bytes: int
    history_rows: int
    channels: int
    feed_cached_channels: int
    feed_newest_age_seconds: float | None = None
    feed_oldest_age_seconds: float | None = None
    lives_active: int


class SponsorSegment(BaseModel):
    category: str
    start: float
    end: float


class SponsorSegmentsOut(BaseModel):
    segments: list[SponsorSegment] = []
