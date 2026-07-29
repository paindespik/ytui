"""Channel view: latest videos of one channel, with 'follow channel' action."""

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


class ChannelScreen(BrowseScreen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("a", "add_this_channel", "Follow channel"),
        Binding("m", "load_more", "Load more"),
    ]

    PAGE_SIZE = 50

    def __init__(self, channel: Video) -> None:
        super().__init__()
        self.channel = channel
        self._loaded: list[Video] = []
        self._has_more = False
        self._loading_page = False

    @property
    def channel_ref(self) -> str:
        return (
            self.channel.video_id if self.channel.kind == "channel" else self.channel.channel_id
        )

    def compose(self) -> ComposeResult:
        yield Header()
        yield LoadingIndicator(id="channel-loading")
        with Horizontal():
            yield VideoList(id="channel-list")
            yield DetailPanel(thumbnails_enabled=self.app.config.ui.thumbnails)
        yield PlayerBar()
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.channel.channel_title or self.channel.title or "Channel"
        self.load_videos()

    @work(exclusive=True, group="reload")
    async def load_videos(self) -> None:
        try:
            videos, has_more = await self.app.client.channel_videos(
                self.channel_ref, platform=self.channel.platform, limit=self.PAGE_SIZE
            )
        except YtuiApiError as exc:
            self._show_error(exc.detail)
            return
        self._loaded = list(videos)
        self._has_more = has_more
        self._show_videos(videos)

    def action_load_more(self) -> None:
        if self._loading_page:
            self.app.notify("Already loading…", timeout=2)
            return
        if not self._has_more:
            self.app.notify("No more videos", timeout=2)
            return
        # Set synchronously: the worker only starts on the next event loop turn,
        # so a second keypress would otherwise slip past the guard.
        self._loading_page = True
        self.load_more_videos()

    @work(exclusive=True, group="channel-page")
    async def load_more_videos(self) -> None:
        loading = self.query_one("#channel-loading", LoadingIndicator)
        loading.display = True
        try:
            videos, has_more = await self.app.client.channel_videos(
                self.channel_ref,
                platform=self.channel.platform,
                limit=self.PAGE_SIZE,
                offset=len(self._loaded),
            )
        except YtuiApiError as exc:
            self.app.notify(f"Failed to load more: {exc.detail}", severity="error", timeout=8)
            return
        finally:
            self._loading_page = False
            loading.display = False
        self._has_more = has_more
        self._loaded.extend(videos)
        video_list = self.query_one("#channel-list", VideoList)
        row = video_list.cursor_row
        video_list.append_videos(videos, self.app.watched)
        if 0 <= row < len(self._loaded):
            video_list.move_cursor(row=row)
        self._update_sub_title()

    def _show_error(self, message: str) -> None:
        self.query_one("#channel-loading", LoadingIndicator).display = False
        self.app.notify(f"Failed to load channel: {message}", severity="error", timeout=8)

    def _show_videos(self, videos: list[Video]) -> None:
        self.query_one("#channel-loading", LoadingIndicator).display = False
        video_list = self.query_one("#channel-list", VideoList)
        video_list.set_videos(list(videos), self.app.watched)
        video_list.focus()
        self._update_sub_title()

    def _update_sub_title(self) -> None:
        name = self.channel.channel_title or self.channel.title or "Channel"
        suffix = "" if self._has_more else " (end)"
        self.sub_title = f"{name} — {len(self._loaded)} videos{suffix}"

    def action_add_this_channel(self) -> None:
        self.app.follow_channel(self.channel)

    def action_go_back(self) -> None:
        self.app.pop_screen()
