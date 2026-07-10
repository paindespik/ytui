"""Search screen: yt-dlp search with an input field and mixed result list."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, LoadingIndicator

from ...sources.ytdlp_source import search_videos
from ..widgets.detail_panel import DetailPanel
from ..widgets.video_list import VideoList
from .browse import BrowseScreen


class SearchScreen(BrowseScreen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("slash", "focus_input", "New search", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Search YouTube…", id="search-input")
        yield LoadingIndicator(id="search-loading")
        with Horizontal():
            yield VideoList(id="search-results")
            yield DetailPanel(thumbnails_enabled=self.app.config.ui.thumbnails)
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Search"
        self.query_one("#search-loading", LoadingIndicator).display = False
        self.query_one("#search-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self.run_search(query)

    @work(exclusive=True, thread=True)
    def run_search(self, query: str) -> None:
        self.app.call_from_thread(self._set_loading, True)
        try:
            videos = search_videos(query, limit=20)
        except Exception as exc:
            self.app.call_from_thread(self._set_loading, False)
            self.app.call_from_thread(
                self.app.notify, f"Search failed: {exc}", severity="error", timeout=8
            )
            return
        self.app.call_from_thread(self._set_loading, False)
        self.app.call_from_thread(self._show_results, videos)

    def _set_loading(self, loading: bool) -> None:
        self.query_one("#search-loading", LoadingIndicator).display = loading

    def _show_results(self, videos) -> None:
        results = self.query_one("#search-results", VideoList)
        results.set_videos(videos)
        if videos:
            results.focus()
        else:
            self.app.notify("No results.", timeout=5)

    def action_go_back(self) -> None:
        if len(self.app.screen_stack) > 2:
            self.app.pop_screen()

    def action_focus_input(self) -> None:
        self.query_one("#search-input", Input).focus()
