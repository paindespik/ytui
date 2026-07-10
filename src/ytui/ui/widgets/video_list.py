"""Video list widget: title / channel / age columns with vim-style navigation."""

from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable

from ...models import Video

_KIND_LABELS = {"video": "", "playlist": "▶ Playlist", "channel": "@ Channel"}


class VideoList(DataTable):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "scroll_top", "Top", show=False),
        Binding("G", "scroll_bottom", "Bottom", show=False),
    ]

    class VideoSelected(Message):
        def __init__(self, video: Video) -> None:
            self.video = video
            super().__init__()

    class VideoHighlighted(Message):
        def __init__(self, video: Video) -> None:
            self.video = video
            super().__init__()

    def __init__(self, **kwargs) -> None:
        super().__init__(cursor_type="row", zebra_stripes=True, **kwargs)
        self._videos: list[Video] = []

    def on_mount(self) -> None:
        self.add_column("Title", key="title")
        self.add_column("Channel", key="channel")
        self.add_column("Age", key="age")
        self.add_column("Type", key="kind")

    def set_videos(self, videos: list[Video]) -> None:
        self._videos = videos
        self.clear()
        for video in videos:
            self.add_row(
                video.title, video.channel_title, video.age, _KIND_LABELS.get(video.kind, "")
            )

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
