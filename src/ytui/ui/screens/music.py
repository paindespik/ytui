"""Music screen: the account's YouTube Music home, shelf by shelf."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Label, LoadingIndicator

from ...api_client import MusicHome, YtuiApiError
from ..widgets.detail_panel import DetailPanel
from ..widgets.player_bar import PlayerBar
from ..widgets.shelf_list import ShelfList
from ..widgets.video_list import VideoList
from .browse import BrowseScreen


class MusicScreen(BrowseScreen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("", id="warning-banner", classes="hidden")
        yield LoadingIndicator(id="music-loading")
        with Horizontal():
            yield ShelfList(id="music-list")
            yield DetailPanel(thumbnails_enabled=self.app.config.ui.thumbnails)
        yield PlayerBar()
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Music"
        self._load(force_refresh=False)

    @work(exclusive=True)
    async def _load(self, force_refresh: bool) -> None:
        loading = self.query_one("#music-loading", LoadingIndicator)
        loading.display = True
        try:
            result: MusicHome = await self.app.client.music(refresh=force_refresh)
        except YtuiApiError as exc:
            loading.display = False
            self._show_warning(f"Failed to load YouTube Music: {exc.detail}")
            return
        loading.display = False
        shelf_list = self.query_one("#music-list", ShelfList)
        shelf_list.set_shelves(result.shelves, self.app.watched)
        shelf_list.focus()
        if result.warnings:
            self._show_warning(" | ".join(result.warnings[:3]))
        elif not result.shelves:
            self._show_warning("YouTube Music returned no shelves.")
        else:
            self._hide_warning()

    def on_video_list_video_selected(self, event: VideoList.VideoSelected) -> None:
        # Tracks and artists fall through to BrowseScreen's handler (Textual
        # runs it after this one unless the default is prevented).
        if event.video.kind == "playlist":
            # Like the site: an album or a mix opens its track list (listed
            # with the account, so personal mixes are the right ones).
            event.prevent_default()
            self.app.open_playlist(event.video)

    def _show_warning(self, text: str) -> None:
        banner = self.query_one("#warning-banner", Label)
        banner.update(f"⚠ {text}")
        banner.remove_class("hidden")

    def _hide_warning(self) -> None:
        self.query_one("#warning-banner", Label).add_class("hidden")

    def action_refresh(self) -> None:
        self._load(force_refresh=True)

    def action_go_back(self) -> None:
        self.app.pop_screen()
