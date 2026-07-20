"""Home feed screen: merged feed of followed channels, served by the backend."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Label, LoadingIndicator

from ...api_client import FeedResult, YtuiApiError
from ...models import Video
from ..widgets.detail_panel import DetailPanel
from ..widgets.player_bar import PlayerBar
from ..widgets.video_list import VideoList
from .browse import BrowseScreen


class HomeFeedScreen(BrowseScreen):
    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("/", "search", "Search"),
        Binding("h", "history", "History"),
        Binding("S", "suggestions", "Suggestions"),
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
        self._feed_videos: list[Video] = []
        self.load_feed(force_refresh=False)

    def focus_live(self, video: Video) -> None:
        """Refresh the feed and focus the given live video (from a notification)."""
        self._pending_focus = video
        self.load_feed(force_refresh=True)

    def _with_lives(self) -> list[Video]:
        """Feed videos with active lives pinned on top (deduplicated).

        Lives are prefixed with a colored dot: \U0001f534 YouTube, \U0001f7e3 Twitch,
        \U0001f3b5 TikTok (the Source column also shows a colored platform label).
        """
        lives = list(self.app.active_lives.values())
        live_ids = {v.video_id for v in lives}
        pinned: list[Video] = []
        for v in lives:
            dot = {"twitch": "\U0001f7e3", "tiktok": "\U0001f3b5"}.get(v.platform, "\U0001f534")
            pinned.append(v.model_copy(update={"title": f"{dot} {v.title}"}))
        return pinned + [v for v in self._feed_videos if v.video_id not in live_ids]

    def refresh_lives(self) -> None:
        """Re-render the list when the set of active lives changed."""
        if not hasattr(self, "_feed_videos"):
            return
        self.query_one("#feed-list", VideoList).set_videos(self._with_lives(), self.app.watched)

    @work(exclusive=True)
    async def load_feed(self, force_refresh: bool, *, silent: bool = False) -> None:
        loading = self.query_one("#feed-loading", LoadingIndicator)
        if not silent:
            loading.display = True
        try:
            result: FeedResult = await self.app.client.feed(refresh=force_refresh)
        except YtuiApiError as exc:
            if not silent:
                loading.display = False
            if exc.status_code == 0:
                self._show_warning(
                    f"Server unreachable: {self.app.config.server.url or '(not configured)'}"
                )
            else:
                self._show_warning(f"Failed to load feed: {exc.detail}")
            return
        if not silent:
            loading.display = False
        self._feed_videos = result.videos
        pending = self._pending_focus
        self._pending_focus = None
        feed_list = self.query_one("#feed-list", VideoList)
        if silent:
            cursor_row = feed_list.cursor_row
        feed_list.set_videos(self._with_lives(), self.app.watched)
        if silent and 0 <= cursor_row < len(self._with_lives()):
            feed_list.move_cursor(row=cursor_row)
        if pending is not None:
            feed_list.focus_video(pending.video_id)
        if result.warnings:
            self._show_warning(" | ".join(result.warnings[:3]))
        else:
            self._hide_warning()
        if not result.videos and not result.warnings:
            self._show_warning("No videos. Add channels in settings (,) or press / to search.")

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

    def action_suggestions(self) -> None:
        self.app.push_screen("suggestions")

    def action_local_playlists(self) -> None:
        self.app.push_screen("local_playlists")

    def action_settings(self) -> None:
        self.app.push_screen("settings")
