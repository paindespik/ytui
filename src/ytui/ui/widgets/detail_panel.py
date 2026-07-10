"""Detail side panel: thumbnail + metadata of the highlighted item."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Label, Static

from ...models import Video

try:
    # Importing textual_image queries the terminal for the cell size; this must
    # happen before the Textual app starts, so keep this a module-level import.
    from textual_image.widget import Image as ThumbImage

    _IMAGE_ERROR: str | None = None
except Exception as exc:  # pragma: no cover - depends on the environment
    ThumbImage = None
    _IMAGE_ERROR = str(exc)


class DetailPanel(Widget):
    """Shows the thumbnail and metadata of the currently highlighted video."""

    DEFAULT_CSS = """
    DetailPanel {
        width: 36;
        padding: 0 1;
        border-left: solid $primary;
    }
    DetailPanel #thumb-slot {
        height: 9;
        margin-bottom: 1;
    }
    DetailPanel #thumb-placeholder {
        height: 9;
        content-align: center middle;
        color: $text-muted;
        background: $surface;
    }
    DetailPanel #detail-title {
        text-style: bold;
    }
    DetailPanel #detail-meta {
        color: $text-muted;
    }
    """

    def __init__(self, thumbnails_enabled: bool = True, **kwargs) -> None:
        super().__init__(**kwargs)
        self._thumbnails_enabled = thumbnails_enabled and ThumbImage is not None
        self._current: Video | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="thumb-slot"):
            if self._thumbnails_enabled:
                yield Static("…", id="thumb-placeholder")
                yield ThumbImage(id="thumb-image")
        yield Label("", id="detail-title")
        yield Label("", id="detail-meta")

    def on_mount(self) -> None:
        if self._thumbnails_enabled:
            self.query_one("#thumb-image").display = False
        else:
            self.query_one("#thumb-slot").display = False

    def show_video(self, video: Video) -> None:
        self._current = video
        self.query_one("#detail-title", Label).update(video.title)
        meta_parts = [video.channel_title, video.age]
        if video.duration:
            mins, secs = divmod(video.duration, 60)
            hours, mins = divmod(mins, 60)
            meta_parts.append(f"{hours}:{mins:02}:{secs:02}" if hours else f"{mins}:{secs:02}")
        if video.kind != "video":
            meta_parts.append(f"[{video.kind}]")
        self.query_one("#detail-meta", Label).update("  ".join(p for p in meta_parts if p))
        if self._thumbnails_enabled:
            self._load_thumbnail(video)

    def _load_thumbnail(self, video: Video) -> None:
        placeholder = self.query_one("#thumb-placeholder", Static)
        image = self.query_one("#thumb-image")
        if not video.thumbnail_url:
            image.display = False
            placeholder.display = True
            placeholder.update("no thumbnail")
            return
        cached = self.app.thumbnails.cached_path(video.thumbnail_url)
        if cached is not None:
            self._set_image(video, cached)
            return
        placeholder.display = True
        image.display = False
        placeholder.update("loading…")
        self._fetch_thumbnail(video)

    def _fetch_thumbnail(self, video: Video) -> None:
        async def fetch() -> None:
            path = await self.app.thumbnails.fetch(video.thumbnail_url)
            if self._current is not video:
                return  # selection moved on while downloading
            if path is None:
                self.query_one("#thumb-placeholder", Static).update("thumbnail unavailable")
                return
            self._set_image(video, path)

        self.run_worker(fetch(), exclusive=True, group="thumb-fetch")

    def _set_image(self, video: Video, path: Path) -> None:
        image = self.query_one("#thumb-image")
        try:
            image.image = str(path)
        except Exception:
            self.query_one("#thumb-placeholder", Static).update("render error")
            return
        self.query_one("#thumb-placeholder", Static).display = False
        image.display = True
