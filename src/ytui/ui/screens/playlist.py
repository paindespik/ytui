"""Playlist view: the playlist's videos; Enter plays one, 'p' plays the whole playlist."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, LoadingIndicator

from ...api_client import YtuiApiError
from ...models import Video
from ..widgets.detail_panel import DetailPanel
from ..widgets.player_bar import PlayerBar
from ..widgets.video_list import VideoList
from .browse import BrowseScreen


def _signed_in_only(playlist: Video) -> bool:
    """YouTube Music playlists the server lists with the account.

    Mirrors services/music.is_personal_playlist: liked music (LM) and the
    personal mixes (RDTMAK\u2026).
    """
    return playlist.platform == "youtube" and (
        playlist.video_id == "LM" or playlist.video_id.startswith("RDTMAK")
    )


class PlaylistScreen(BrowseScreen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("p", "play_all", "Play whole playlist"),
        Binding("S", "import_playlist", "Import to local playlist"),
    ]

    def __init__(self, playlist: Video) -> None:
        super().__init__()
        self.playlist = playlist
        self._videos: list[Video] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield LoadingIndicator(id="playlist-loading")
        with Horizontal():
            yield VideoList(id="playlist-list")
            yield DetailPanel(thumbnails_enabled=self.app.config.ui.thumbnails)
        yield PlayerBar()
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.playlist.title or "Playlist"
        self.load_videos()

    @work(exclusive=True)
    async def load_videos(self) -> None:
        try:
            videos = await self.app.client.playlist_videos(
                self.playlist.video_id, platform=self.playlist.platform
            )
        except YtuiApiError as exc:
            self._show_error(exc.detail)
            return
        self._show_videos(videos)

    def _show_error(self, message: str) -> None:
        self.query_one("#playlist-loading", LoadingIndicator).display = False
        self.app.notify(f"Failed to load playlist: {message}", severity="error", timeout=8)

    def _show_videos(self, videos: list[Video]) -> None:
        self.query_one("#playlist-loading", LoadingIndicator).display = False
        for v in videos:
            v.playlist_id = self.playlist.video_id
        self._videos = videos
        video_list = self.query_one("#playlist-list", VideoList)
        video_list.set_videos(videos, self.app.watched)
        video_list.focus()

    def action_play_all(self) -> None:
        if self._videos and _signed_in_only(self.playlist):
            # mpv would list it anonymously: generic tracks, or nothing at all.
            self.app.play_listed(self._videos)
            return
        self.app.play_video(self.playlist)

    def action_import_playlist(self) -> None:
        # This screen lists videos: import the playlist being browsed, not the row.
        self.app.import_playlist_to_local(
            self.playlist.video_id, self.playlist.platform, self.playlist.title
        )

    def action_go_back(self) -> None:
        self.app.pop_screen()
