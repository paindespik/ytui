"""Suggestions screen: history-based recommendations served by the backend."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Label, LoadingIndicator

from ...api_client import FeedResult, YtuiApiError
from ..widgets.detail_panel import DetailPanel
from ..widgets.player_bar import PlayerBar
from ..widgets.video_list import VideoList
from .browse import BrowseScreen


class SuggestionsScreen(BrowseScreen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("", id="warning-banner", classes="hidden")
        yield LoadingIndicator(id="suggestions-loading")
        with Horizontal():
            yield VideoList(id="suggestions-list")
            yield DetailPanel(thumbnails_enabled=self.app.config.ui.thumbnails)
        yield PlayerBar()
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Suggestions"
        self._load(force_refresh=False)

    @work(exclusive=True)
    async def _load(self, force_refresh: bool) -> None:
        loading = self.query_one("#suggestions-loading", LoadingIndicator)
        loading.display = True
        try:
            result: FeedResult = await self.app.client.suggestions(refresh=force_refresh)
        except YtuiApiError as exc:
            loading.display = False
            self._show_warning(f"Failed to load suggestions: {exc.detail}")
            return
        loading.display = False
        video_list = self.query_one("#suggestions-list", VideoList)
        video_list.set_videos(result.videos, self.app.watched)
        video_list.focus()
        if result.warnings:
            self._show_warning(" | ".join(result.warnings[:3]))
        else:
            self._hide_warning()

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
