"""Full-screen video detail: description, views, likes, duration, date + actions."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, LoadingIndicator, Static

from ...models import Video
from ...sources.ytdlp_source import VideoDetails, video_details


def _fmt_count(count: int | None) -> str:
    if count is None:
        return "?"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def _fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return "?"
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    return f"{hours}:{mins:02}:{secs:02}" if hours else f"{mins}:{secs:02}"


class VideoDetailScreen(Screen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("enter", "play", "Play"),
        Binding("A", "play_audio", "Audio only"),
        Binding("e", "enqueue", "Enqueue"),
        Binding("y", "copy_url", "Copy URL"),
        Binding("o", "open_channel", "Open channel"),
        Binding("d", "download", "Download"),
        Binding("s", "save_to_playlist", "Save to playlist", show=False),
        Binding("question_mark", "help", "Help", show=False),
    ]

    DEFAULT_CSS = """
    VideoDetailScreen #detail-body {
        padding: 1 2;
    }
    VideoDetailScreen #detail-heading {
        text-style: bold;
    }
    VideoDetailScreen #detail-stats {
        color: $text-muted;
        margin-bottom: 1;
    }
    """

    def __init__(self, video: Video) -> None:
        super().__init__()
        self.video = video
        self._details: VideoDetails | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield LoadingIndicator(id="detail-loading")
        with VerticalScroll(id="detail-body"):
            yield Label(self.video.title, id="detail-heading")
            yield Label("", id="detail-stats")
            yield Static("", id="detail-description")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Video details"
        self.load_details()

    @work(exclusive=True, thread=True)
    def load_details(self) -> None:
        try:
            details = video_details(self.video.url)
        except Exception as exc:
            self.app.call_from_thread(self._show_error, str(exc))
            return
        self.app.call_from_thread(self._show_details, details)

    def _show_error(self, message: str) -> None:
        self.query_one("#detail-loading", LoadingIndicator).display = False
        self.query_one("#detail-stats", Label).update("Could not load details.")
        self.app.notify(f"Failed to load details: {message}", severity="error", timeout=8)

    def _show_details(self, details: VideoDetails) -> None:
        self._details = details
        # Backfill channel info so 'o' works from search results without channel_id.
        if not self.video.channel_id and details.channel_id:
            self.video.channel_id = details.channel_id
        if not self.video.channel_title and details.channel_title:
            self.video.channel_title = details.channel_title
        self.query_one("#detail-loading", LoadingIndicator).display = False
        if details.title:
            self.query_one("#detail-heading", Label).update(details.title)
        stats = (
            f"{self.video.channel_title or details.channel_title}"
            f"  ·  {_fmt_count(details.view_count)} views"
            f"  ·  {_fmt_count(details.like_count)} likes"
            f"  ·  {_fmt_duration(details.duration)}"
            f"  ·  {details.upload_date or '?'}"
        )
        self.query_one("#detail-stats", Label).update(stats)
        self.query_one("#detail-description", Static).update(details.description or "(no description)")

    # -- actions --

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_play(self) -> None:
        self.app.play_video(self.video)

    def action_play_audio(self) -> None:
        self.app.play_video(self.video, audio_only=True)

    def action_enqueue(self) -> None:
        self.app.enqueue_video(self.video)

    def action_copy_url(self) -> None:
        self.app.copy_to_clipboard(self.video.url)  # emits OSC 52 (works over SSH in foot)
        self.app.notify("URL copied to clipboard.", timeout=4)

    def action_open_channel(self) -> None:
        self.app.open_channel(self.video)

    def action_download(self) -> None:
        self.app.download_video(self.video)

    def action_save_to_playlist(self) -> None:
        self.app.save_to_local_playlist(self.video)

    def action_help(self) -> None:
        self.app.push_screen("help")
