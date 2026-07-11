"""Home feed screen: merged RSS feed of configured channels."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Label, LoadingIndicator

from ...models import Video
from ...sources.base import FeedResult
from ..widgets.detail_panel import DetailPanel
from ..widgets.player_bar import PlayerBar
from ..widgets.video_list import VideoList
from .browse import BrowseScreen


class HomeFeedScreen(BrowseScreen):
    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("/", "search", "Search"),
        Binding("h", "history", "History"),
        Binding("P", "local_playlists", "Playlists"),
        Binding("comma", "settings", "Settings"),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("", id="warning-banner", classes="hidden")
        yield LoadingIndicator(id="feed-loading")
        with Horizontal():
            yield VideoList(id="feed-list")
            yield DetailPanel(thumbnails_enabled=self.app.config.ui.thumbnails)
        yield PlayerBar()
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Home feed"
        self._pending_focus: Video | None = None
        self.load_feed(force_refresh=False)

    def focus_live(self, video: Video) -> None:
        """Refresh the feed and focus the given live video (from a notification)."""
        self._pending_focus = video
        self.load_feed(force_refresh=True)

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
        videos = result.videos
        pending = self._pending_focus
        self._pending_focus = None
        if pending is not None and all(v.video_id != pending.video_id for v in videos):
            videos = [pending, *videos]  # live not (yet) in the RSS feed: show it on top
        feed_list = self.query_one("#feed-list", VideoList)
        feed_list.set_videos(videos, self.app.cache.watched_ids())
        if pending is not None:
            feed_list.focus_video(pending.video_id)
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

    def action_history(self) -> None:
        self.app.push_screen("history")

    def action_local_playlists(self) -> None:
        self.app.push_screen("local_playlists")

    def action_settings(self) -> None:
        self.app.push_screen("settings")
