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
    ]

    def __init__(self, channel: Video) -> None:
        super().__init__()
        self.channel = channel

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

    @work(exclusive=True)
    async def load_videos(self) -> None:
        try:
            videos = await self.app.client.channel_videos(
                self.channel_ref, platform=self.channel.platform
            )
        except YtuiApiError as exc:
            self._show_error(exc.detail)
            return
        self._show_videos(videos)

    def _show_error(self, message: str) -> None:
        self.query_one("#channel-loading", LoadingIndicator).display = False
        self.app.notify(f"Failed to load channel: {message}", severity="error", timeout=8)

    def _show_videos(self, videos: list[Video]) -> None:
        self.query_one("#channel-loading", LoadingIndicator).display = False
        video_list = self.query_one("#channel-list", VideoList)
        video_list.set_videos(videos, self.app.watched)
        video_list.focus()

    def action_add_this_channel(self) -> None:
        self.app.follow_channel(self.channel)

    def action_go_back(self) -> None:
        self.app.pop_screen()
