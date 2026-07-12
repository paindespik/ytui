"""Search screen: server-side search with an input field and mixed result list."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, LoadingIndicator

from ...api_client import YtuiApiError
from ..widgets.detail_panel import DetailPanel
from ..widgets.player_bar import PlayerBar
from ..widgets.video_list import VideoList
from .browse import BrowseScreen


SOURCES = ("youtube", "odysee")
SOURCE_LABELS = {"youtube": "YouTube", "odysee": "Odysee"}


class SearchScreen(BrowseScreen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("slash", "focus_input", "New search", show=False),
        Binding("ctrl+s", "toggle_source", "Source"),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.source: str = "youtube"
        self._last_query: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Search YouTube…", id="search-input")
        yield LoadingIndicator(id="search-loading")
        with Horizontal():
            yield VideoList(id="search-results")
            yield DetailPanel(thumbnails_enabled=self.app.config.ui.thumbnails)
        yield PlayerBar()
        yield Footer()

    def on_mount(self) -> None:
        self._update_source_ui()
        self.query_one("#search-loading", LoadingIndicator).display = False
        self.query_one("#search-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self.run_search(query)

    @work(exclusive=True)
    async def run_search(self, query: str) -> None:
        self._last_query = query
        self._set_loading(True)
        try:
            videos = await self.app.client.search(query, limit=20, source=self.source)
        except YtuiApiError as exc:
            self._set_loading(False)
            self.app.notify(f"Search failed: {exc.detail}", severity="error", timeout=8)
            return
        self._set_loading(False)
        self._show_results(videos)

    def _set_loading(self, loading: bool) -> None:
        self.query_one("#search-loading", LoadingIndicator).display = loading

    def _show_results(self, videos) -> None:
        results = self.query_one("#search-results", VideoList)
        results.set_videos(videos, self.app.watched)
        if videos:
            results.focus()
        else:
            self.app.notify("No results.", timeout=5)

    def action_go_back(self) -> None:
        if len(self.app.screen_stack) > 2:
            self.app.pop_screen()

    def action_focus_input(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_toggle_source(self) -> None:
        index = SOURCES.index(self.source)
        self.source = SOURCES[(index + 1) % len(SOURCES)]
        self._update_source_ui()
        if self._last_query:
            self.run_search(self._last_query)

    def _update_source_ui(self) -> None:
        label = SOURCE_LABELS[self.source]
        self.sub_title = f"Search — {label}"
        self.query_one("#search-input", Input).placeholder = f"Search {label}…"
