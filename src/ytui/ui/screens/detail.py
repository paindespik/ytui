"""Full-screen video detail: description, views, likes, duration, date + actions."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, LoadingIndicator, Static

from ...api_client import YtuiApiError
from ...models import CommentPage, Video, VideoDetails

# Platforms whose comments the backend can list.
COMMENT_PLATFORMS = ("youtube", "odysee")


def _fmt_count(count: int | None) -> str:
    if count is None:
        return "?"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def _fmt_comment_date(timestamp: int | None) -> str:
    if not timestamp:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")


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
        Binding("L", "like", "Like", show=False),
        Binding("C", "comment", "Comment", show=False),
        Binding("c", "reload_comments", "Comments", show=False),
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
    VideoDetailScreen #detail-comments-title {
        margin-top: 1;
        text-style: bold;
        display: none;
    }
    VideoDetailScreen #detail-comments-title.visible {
        display: block;
    }
    VideoDetailScreen .detail-comment {
        margin-top: 1;
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
            yield Static("", id="detail-comments-title")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Video details"
        self.load_details()

    @work(exclusive=True)
    async def load_details(self) -> None:
        try:
            details = await self.app.client.video_details(
                self.video.video_id, platform=self.video.platform
            )
        except YtuiApiError as exc:
            self._show_error(exc.detail)
            return
        self._show_details(details)
        if self.video.platform in COMMENT_PLATFORMS:
            self.load_comments()

    @work(exclusive=True, group="comments")
    async def load_comments(self) -> None:
        title = self.query_one("#detail-comments-title", Static)
        title.add_class("visible")
        title.update("Comments — loading…")
        try:
            page = await self.app.client.video_comments(
                self.video.video_id, platform=self.video.platform
            )
        except YtuiApiError as exc:
            title.update(f"Comments unavailable: {exc.detail}")
            return
        self._show_comments(page)

    def _show_comments(self, page: CommentPage) -> None:
        body = self.query_one("#detail-body", VerticalScroll)
        for old in self.query(".detail-comment"):
            old.remove()
        title = self.query_one("#detail-comments-title", Static)
        if page.disabled:
            title.update("Comments disabled")
            return
        comments = page.items
        if not comments:
            title.update("Comments (0)")
            return
        title.update(f"Comments ({page.total or len(comments)})")
        for comment in comments:
            pin = "📌 " if comment.is_pinned else ""
            date = _fmt_comment_date(comment.timestamp)
            header = f"{pin}[b]{escape(comment.channel_name)}[/b]"
            if date:
                header += f"  [dim]{date}[/dim]"
            if comment.likes:
                header += f"  👍{comment.likes}"
            if comment.replies:
                header += f"  [dim]({comment.replies} replies)[/dim]"
            widget = Static(f"{header}\n{escape(comment.text)}", classes="detail-comment")
            body.mount(widget)

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

    def action_like(self) -> None:
        self.app.like_video_action(self.video)

    def action_comment(self) -> None:
        self.app.comment_video_action(self.video)

    def action_reload_comments(self) -> None:
        if self.video.platform not in COMMENT_PLATFORMS:
            self.app.notify(
                "Comment listing is available for YouTube and Odysee only.", timeout=5
            )
            return
        self.load_comments()

    def action_help(self) -> None:
        self.app.push_screen("help")
