"""Video list widget: title / channel / age / source columns with vim-style navigation.

With ``thumbnails=True`` (web-style mode) the list gains a thumbnail column
rendered through the terminal image protocol (sixel/kitty) or as half-block
text; rows are taller and carry the full item info, so the side DetailPanel
is not needed in that mode.
"""

from __future__ import annotations

from typing import Protocol, cast

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable
from textual_image._terminal import get_cell_size

from ...models import Video
from ...thumbnails.fetcher import ThumbnailFetcher
from .thumbs import ThumbImage, centered

_KIND_MARKS = {"playlist": "▶ ", "channel": "@ "}
_PLATFORM_LABELS = {
    "youtube": "YouTube",
    "bitchute": "BitChute",
    "odysee": "Odysee",
    "twitch": "Twitch",
    "tiktok": "TikTok",
    "crowdbunker": "CrowdBunker",
}
_TWITCH_STYLE = "#a970ff"  # Twitch brand purple
_TIKTOK_STYLE = "#ff0050"  # TikTok brand red-pink
_PLATFORM_STYLES = {"twitch": _TWITCH_STYLE, "tiktok": _TIKTOK_STYLE}


def _source_label(video: Video) -> str:
    """'YouTube', '▶ Odysee' (playlist), '@ BitChute' (channel)…"""
    mark = _KIND_MARKS.get(video.kind, "")
    return mark + _PLATFORM_LABELS.get(video.platform, video.platform)


class _ThumbnailApp(Protocol):
    thumbnails: ThumbnailFetcher


class VideoList(DataTable):
    MIN_THUMB_WIDTH = 11
    THUMB_ROWS = 6

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "scroll_top", "Top", show=False),
        Binding("G", "scroll_bottom", "Bottom", show=False),
        Binding("right", "select_video", "Play video", show=False),
    ]

    class VideoSelected(Message):
        def __init__(self, video: Video) -> None:
            self.video = video
            super().__init__()

    class VideoHighlighted(Message):
        def __init__(self, video: Video) -> None:
            self.video = video
            super().__init__()

    def __init__(self, mark_watched: bool = True, thumbnails: bool = True, **kwargs) -> None:
        super().__init__(cursor_type="row", zebra_stripes=True, **kwargs)
        self._videos: list[Video] = []
        self._mark_watched = mark_watched
        self._thumbnails = thumbnails
        self._thumb_width = self._compute_thumb_width()
        # Bumped on every full rebuild so in-flight thumbnail fetches for the
        # previous list are dropped instead of updating stale rows.
        self._gen = 0
        self._fetching: set[str] = set()

    @classmethod
    def _compute_thumb_width(cls) -> int:
        """Thumbnail width in cells so the displayed image stays 16:9.

        Terminal cells are roughly 2:1 (width:height) in pixels, so a fixed
        11x6 cell box is nearly square on screen and squashes the image. The
        width is derived from the real cell size instead.
        """
        try:
            cell_w, cell_h = get_cell_size()
        except Exception:
            cell_w = cell_h = 0
        if not cell_w or not cell_h:
            return 21
        return max(
            cls.MIN_THUMB_WIDTH,
            round(cls.THUMB_ROWS * (16 / 9) * cell_h / cell_w),
        )

    @property
    def thumbnails(self) -> bool:
        return self._thumbnails

    def on_mount(self) -> None:
        self.add_column(" ", key="watched")
        if self._thumbnails:
            self.add_column(" ", key="thumb")
        self.add_column("Title", key="title")
        self.add_column("Channel", key="channel")
        self.add_column("Age", key="age")
        self.add_column("Source", key="source")

    @staticmethod
    def _row_key(row: int, video: Video) -> str:
        """Stable per-position key (position prefix disambiguates duplicates)."""
        return f"r:{row}:{video.platform}:{video.video_id}"

    def _thumb_cell(self, video: Video, row: int):
        if not video.thumbnail_url or self.app is None:
            return centered("–" if video.thumbnail_url else "–", self.THUMB_ROWS)
        app = cast("_ThumbnailApp", self.app)
        path = app.thumbnails.cached_path(video.thumbnail_url)
        if path is not None:
            try:
                return ThumbImage(path, width=self._thumb_width, height=self.THUMB_ROWS)
            except Exception:
                return centered("–", self.THUMB_ROWS)
        self._ensure_fetch(video, row)
        return centered("…", self.THUMB_ROWS)

    def _ensure_fetch(self, video: Video, row: int) -> None:
        key = f"{video.platform}:{video.video_id}"
        if key in self._fetching or not video.thumbnail_url:
            return
        self._fetching.add(key)
        gen = self._gen
        app = cast("_ThumbnailApp", self.app)

        async def fetch() -> None:
            try:
                path = await app.thumbnails.fetch(video.thumbnail_url)
            except Exception:
                path = None
            finally:
                self._fetching.discard(key)
            if path is None or self._gen != gen:
                return
            try:
                self.update_cell(
                    self._row_key(row, video),
                    "thumb",
                    ThumbImage(path, width=self._thumb_width, height=self.THUMB_ROWS),
                    update_width=True,
                )
            except Exception:
                pass  # widget detached or row gone meanwhile

        self.run_worker(fetch(), group="thumb-fetch")

    def _add_video_row(self, video: Video, watched: set[str], row: int) -> None:
        seen = f"{video.platform}:{video.video_id}" in watched
        style = "dim" if seen else ""
        source = Text(
            _source_label(video),
            style=f"{style} {_PLATFORM_STYLES.get(video.platform, '')}".strip(),
        )
        cells: list = [Text("✓" if seen else "", style=style)]
        if self._thumbnails:
            cells.append(self._thumb_cell(video, row))
            cells.extend(
                centered(cell, self.THUMB_ROWS)
                for cell in (
                    Text(video.title, style=style),
                    Text(video.channel_title, style=style),
                    Text(video.age, style=style),
                    source,
                )
            )
        else:
            cells.extend(
                (
                    Text(video.title, style=style),
                    Text(video.channel_title, style=style),
                    Text(video.age, style=style),
                    source,
                )
            )
        self.add_row(
            *cells,
            height=self.THUMB_ROWS if self._thumbnails else 1,
            key=self._row_key(row, video),
        )

    def set_videos(self, videos: list[Video], watched: set[str] | None = None) -> None:
        self._videos = videos
        self._gen += 1
        self._fetching.clear()
        watched = watched or set()
        self.clear()
        for row, video in enumerate(videos):
            self._add_video_row(video, watched, row)

    def append_videos(self, videos: list[Video], watched: set[str] | None = None) -> None:
        """Add a page of videos below the current ones, keeping the table intact."""
        watched = watched or set()
        base = len(self._videos)
        self._videos.extend(videos)
        for offset, video in enumerate(videos):
            self._add_video_row(video, watched, base + offset)

    def refresh_watched(self, watched: set[str]) -> None:
        """Re-render watched markers, keeping the cursor position."""
        if not self._mark_watched:
            return
        row = self.cursor_row
        self.set_videos(self._videos, watched)
        if 0 <= row < len(self._videos):
            self.move_cursor(row=row)

    def focus_video(self, video_id: str) -> bool:
        """Move the cursor to the given video. False if it is not in the list."""
        for row, video in enumerate(self._videos):
            if video.video_id == video_id:
                self.move_cursor(row=row)
                self.focus()
                return True
        return False

    def video_at_cursor(self) -> Video | None:
        if 0 <= self.cursor_row < len(self._videos):
            return self._videos[self.cursor_row]
        return None

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        if 0 <= event.cursor_row < len(self._videos):
            self.post_message(self.VideoSelected(self._videos[event.cursor_row]))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        if 0 <= event.cursor_row < len(self._videos):
            self.post_message(self.VideoHighlighted(self._videos[event.cursor_row]))

    def action_select_video(self) -> None:
        """Select the highlighted video (triggered by right arrow)."""
        if 0 <= self.cursor_row < len(self._videos):
            self.post_message(self.VideoSelected(self._videos[self.cursor_row]))
