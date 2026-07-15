"""Video list widget: title / channel / age / source columns with vim-style navigation."""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable

from ...models import Video

_KIND_MARKS = {"playlist": "▶ ", "channel": "@ "}
_PLATFORM_LABELS = {"youtube": "YouTube", "bitchute": "BitChute", "odysee": "Odysee"}


def _source_label(video: Video) -> str:
    """'YouTube', '▶ Odysee' (playlist), '@ BitChute' (channel)…"""
    mark = _KIND_MARKS.get(video.kind, "")
    return mark + _PLATFORM_LABELS.get(video.platform, video.platform)


class VideoList(DataTable):
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

    def __init__(self, mark_watched: bool = True, **kwargs) -> None:
        super().__init__(cursor_type="row", zebra_stripes=True, **kwargs)
        self._videos: list[Video] = []
        self._mark_watched = mark_watched

    def on_mount(self) -> None:
        self.add_column(" ", key="watched")
        self.add_column("Title", key="title")
        self.add_column("Channel", key="channel")
        self.add_column("Age", key="age")
        self.add_column("Source", key="source")

    def set_videos(self, videos: list[Video], watched: set[str] | None = None) -> None:
        self._videos = videos
        watched = watched or set()
        self.clear()
        for video in videos:
            seen = video.video_id in watched
            style = "dim" if seen else ""
            self.add_row(
                Text("✓" if seen else "", style=style),
                Text(video.title, style=style),
                Text(video.channel_title, style=style),
                Text(video.age, style=style),
                Text(_source_label(video), style=style),
            )

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
