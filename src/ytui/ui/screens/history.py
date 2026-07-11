"""Watch history screen: most recent plays first, Enter replays, x removes."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header

from ..widgets.detail_panel import DetailPanel
from ..widgets.player_bar import PlayerBar
from ..widgets.video_list import VideoList
from .browse import BrowseScreen


class HistoryScreen(BrowseScreen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("x", "remove_entry", "Remove entry"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield VideoList(id="history-list", mark_watched=False)
            yield DetailPanel(thumbnails_enabled=self.app.config.ui.thumbnails)
        yield PlayerBar()
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Watch history"

    def on_screen_resume(self) -> None:
        self._reload()

    def _reload(self) -> None:
        video_list = self.query_one("#history-list", VideoList)
        video_list.set_videos(self.app.cache.watch_history())
        video_list.focus()

    def on_video_list_video_selected(self, event: VideoList.VideoSelected) -> None:
        video = event.video
        if video.kind == "channel":
            self.app.open_channel(video)
        else:
            self.app.play_from_history(video)

    def action_remove_entry(self) -> None:
        video = self._highlighted()
        if video is None:
            return
        self.app.cache.remove_watch(video.video_id)
        self._reload()
        self.app._refresh_watched_markers()

    def action_go_back(self) -> None:
        self.app.pop_screen()
