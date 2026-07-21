"""Read-only live-chat screen: polls the backend and streams messages."""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from ...api_client import YtuiApiError
from ...models import Video

CHAT_POLL_SECONDS = 3


class LiveChatScreen(Screen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
    ]

    DEFAULT_CSS = """
    LiveChatScreen #chat-body {
        padding: 1 2;
    }
    LiveChatScreen .chat-msg {
        margin-bottom: 1;
    }
    """

    def __init__(self, video: Video) -> None:
        super().__init__()
        self.video = video
        self._cursor = 0
        self._ended = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="chat-body")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f"Live chat · {self.video.channel_title}"
        self._interval = self.set_interval(CHAT_POLL_SECONDS, self._start_poll)
        self._start_poll()

    def _start_poll(self) -> None:
        self.run_worker(self._poll(), group="live-chat", exclusive=True)

    async def _poll(self) -> None:
        try:
            msgs, cursor, active = await self.app.client.live_chat(
                self.video.video_id, self.video.platform, self._cursor
            )
        except YtuiApiError:
            return
        self._cursor = cursor
        body = self.query_one("#chat-body", VerticalScroll)
        for msg in msgs:
            tag = msg.color or "bold"
            body.mount(
                Static(
                    f"[{tag}]{escape(msg.author)}[/]  {escape(msg.text)}",
                    classes="chat-msg",
                )
            )
        for old in self.query(".chat-msg")[:-200]:
            old.remove()
        body.scroll_end(animate=False)
        if not active and not self._ended:
            self._ended = True
            body.mount(Static("— live chat ended —"))
            self._interval.stop()

    def action_go_back(self) -> None:
        self.app.pop_screen()
