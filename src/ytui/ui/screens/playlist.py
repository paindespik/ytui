"""Playlist view: the playlist's videos; Enter plays one, 'p' plays the whole playlist."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, LoadingIndicator

from ...models import Video
from ...sources.ytdlp_source import playlist_videos
from ..widgets.detail_panel import DetailPanel
from ..widgets.video_list import VideoList
from .browse import BrowseScreen


class PlaylistScreen(BrowseScreen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("p", "play_all", "Play whole playlist"),
    ]

    def __init__(self, playlist: Video) -> None:
        super().__init__()
        self.playlist = playlist

    def compose(self) -> ComposeResult:
        yield Header()
        yield LoadingIndicator(id="playlist-loading")
        with Horizontal():
            yield VideoList(id="playlist-list")
            yield DetailPanel(thumbnails_enabled=self.app.config.ui.thumbnails)
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.playlist.title or "Playlist"
        self.load_videos()

    @work(exclusive=True, thread=True)
    def load_videos(self) -> None:
        try:
            videos = playlist_videos(self.playlist.url)
        except Exception as exc:
            self.app.call_from_thread(self._show_error, str(exc))
            return
        self.app.call_from_thread(self._show_videos, videos)

    def _show_error(self, message: str) -> None:
        self.query_one("#playlist-loading", LoadingIndicator).display = False
        self.app.notify(f"Failed to load playlist: {message}", severity="error", timeout=8)

    def _show_videos(self, videos: list[Video]) -> None:
        self.query_one("#playlist-loading", LoadingIndicator).display = False
        video_list = self.query_one("#playlist-list", VideoList)
        video_list.set_videos(videos)
        video_list.focus()

    def action_play_all(self) -> None:
        self.app.play_video(self.playlist.url)

    def action_go_back(self) -> None:
        self.app.pop_screen()
