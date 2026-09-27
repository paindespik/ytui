"""VideoList split into titled shelves, like the YouTube Music home."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from rich.text import Text
from textual.binding import Binding
from textual.widgets import DataTable

from ...models import Video
from .video_list import VideoList


class Shelf(Protocol):
    title: str
    strapline: str
    items: list[Video]


class ShelfList(VideoList):
    """One table for every shelf: a header row per shelf, then its items.

    Header rows are not selectable: the cursor jumps over them in the
    direction it was moving. J / K jump to the next / previous shelf.
    """

    BINDINGS = [
        Binding("J", "next_shelf", "Next shelf"),
        Binding("K", "previous_shelf", "Prev shelf"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._shelves: Sequence[Shelf] = []
        # One entry per table row: None for a shelf header.
        self._rows: list[Video | None] = []
        self._last_row = 0

    def set_shelves(self, shelves: Sequence[Shelf], watched: set[str] | None = None) -> None:
        self._shelves = shelves
        watched = watched or set()
        self._rows = []
        self.clear()
        for shelf in shelves:
            self.add_row(
                Text(""),
                Text(f"♪ {shelf.title}", style="bold"),
                Text(shelf.strapline.lower(), style="dim italic"),
                Text(""),
                Text(""),
            )
            self._rows.append(None)
            for video in shelf.items:
                self._add_video_row(video, watched)
                self._rows.append(video)
        self._videos = [video for video in self._rows if video is not None]
        first = self._nearest_item(0, +1)
        if first is not None:
            self._last_row = first
            self.move_cursor(row=first)

    def set_videos(self, videos: list[Video], watched: set[str] | None = None) -> None:
        """Plain list: a single untitled shelf is not what callers want here."""
        raise NotImplementedError("ShelfList holds shelves: use set_shelves()")

    def _video_at(self, row: int) -> Video | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def _nearest_item(self, row: int, direction: int) -> int | None:
        """First item row from `row` going `direction`, else the other way."""
        for step in (direction, -direction):
            current = row
            while 0 <= current < len(self._rows):
                if self._rows[current] is not None:
                    return current
                current += step
        return None

    def _shelf_starts(self) -> list[int]:
        return [
            row + 1
            for row, video in enumerate(self._rows)
            if video is None and self._video_at(row + 1) is not None
        ]

    def video_at_cursor(self) -> Video | None:
        return self._video_at(self.cursor_row)

    def focus_video(self, video_id: str) -> bool:
        for row, video in enumerate(self._rows):
            if video is not None and video.video_id == video_id:
                self.move_cursor(row=row)
                self.focus()
                return True
        return False

    def refresh_watched(self, watched: set[str]) -> None:
        if not self._mark_watched:
            return
        row = self.cursor_row
        self.set_shelves(self._shelves, watched)
        if 0 <= row < len(self._rows):
            self._last_row = row
            self.move_cursor(row=row)

    # Textual also runs the base class handlers unless prevent_default():
    # VideoList's would index its flat list with a row number that counts
    # the header rows.

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        event.prevent_default()
        video = self._video_at(event.cursor_row)
        if video is not None:
            self.post_message(self.VideoSelected(video))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        event.prevent_default()
        row = event.cursor_row
        if row != self.cursor_row:
            # Stale: clear()/set_shelves() moved the cursor since. Acting on
            # it would yank the cursor back to the top after a repaint.
            return
        video = self._video_at(row)
        if video is None:
            target = self._nearest_item(row, +1 if row >= self._last_row else -1)
            if target is not None and target != row:
                self.move_cursor(row=target)
            return
        self._last_row = row
        self.post_message(self.VideoHighlighted(video))

    def action_select_video(self) -> None:
        video = self.video_at_cursor()
        if video is not None:
            self.post_message(self.VideoSelected(video))

    def action_next_shelf(self) -> None:
        for start in self._shelf_starts():
            if start > self.cursor_row:
                self._last_row = start
                self.move_cursor(row=start)
                return

    def action_previous_shelf(self) -> None:
        """Start of the current shelf, or of the previous one when already there."""
        earlier = [start for start in self._shelf_starts() if start < self.cursor_row]
        if earlier:
            self._last_row = earlier[-1]
            self.move_cursor(row=earlier[-1])
