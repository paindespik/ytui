"""Home feed screen: merged RSS feed of configured channels."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, LoadingIndicator

from ...sources.base import FeedResult
from ..widgets.video_list import VideoList


class HomeFeedScreen(Screen):
    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("/", "search", "Search"),
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "help", "Help"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("", id="warning-banner", classes="hidden")
        yield LoadingIndicator(id="feed-loading")
        yield VideoList(id="feed-list")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Home feed"
        self.load_feed(force_refresh=False)

    @work(exclusive=True)
    async def load_feed(self, force_refresh: bool) -> None:
        loading = self.query_one("#feed-loading", LoadingIndicator)
        loading.display = True
        try:
            result: FeedResult = await self.app.source.feed(force_refresh=force_refresh)
        except Exception as exc:
            loading.display = False
            self._show_warning(f"Failed to load feed: {exc}")
            return
        loading.display = False
        self.query_one("#feed-list", VideoList).set_videos(result.videos)
        if result.warnings:
            self._show_warning(" | ".join(result.warnings[:3]))
        else:
            self._hide_warning()
        if not result.videos and not result.warnings:
            self._show_warning("No videos. Add channels to the config or press / to search.")

    def _show_warning(self, text: str) -> None:
        banner = self.query_one("#warning-banner", Label)
        banner.update(f"⚠ {text}")
        banner.remove_class("hidden")

    def _hide_warning(self) -> None:
        self.query_one("#warning-banner", Label).add_class("hidden")

    def action_refresh(self) -> None:
        self.load_feed(force_refresh=True)

    def action_search(self) -> None:
        self.app.push_screen("search")

    def action_help(self) -> None:
        self.app.push_screen("help")

    def on_video_list_video_selected(self, event: VideoList.VideoSelected) -> None:
        self.app.play_video(event.video.url)
