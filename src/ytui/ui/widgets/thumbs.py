"""Rich renderables for small list-row thumbnails (web-style video lists).

The renderable is auto-selected from ``textual-image`` at import time:
sixel, kitty (TGP), half-block text or unicode blocks, depending on the
terminal. Sixel re-encodes the image on every redraw (rows redraw while
scrolling), so the sixel variant caches the encoded payload per file.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Group
from rich.control import Control
from rich.measure import Measurement
from rich.segment import Segment
from rich.text import Text
from textual_image._terminal import get_cell_size
from textual_image.renderable import Image as AutoImage
from textual_image.renderable.sixel import Image as SixelImage

# Query the terminal once while it is still directly readable (Textual's
# stdin thread steals answers once the app is running). The result is cached
# by the library and reused by every renderable.
try:  # pragma: no cover - no query outside a TTY
    get_cell_size()
except Exception:
    pass

# (file key, pixel size) -> encoded sixel payload
_SIXEL_CACHE: dict[tuple, str] = {}
_MAX_CACHE_ENTRIES = 512  # a few MB at most; cleared wholesale when full


class _CachedSixelImage(SixelImage):
    """SixelImage that encodes each image at most once per pixel size."""

    def __init__(self, path, width=None, height=None) -> None:
        try:
            mtime = Path(path).stat().st_mtime_ns
        except OSError:
            mtime = -1
        self._file_key: tuple = (str(path), mtime)
        super().__init__(path, width, height)

    def __rich_console__(self, console, options):
        from textual_image.renderable.sixel import _NULL_CONTROL
        from textual_image._sixel import image_to_sixels

        terminal_sizes = get_cell_size()
        cell_width, cell_height = self._render_size.get_cell_size(
            options.max_width, options.max_height, terminal_sizes
        )
        pixel_width, pixel_height = self._render_size.get_pixel_size(
            options.max_width, options.max_height, terminal_sizes
        )
        key = (self._file_key, pixel_width, pixel_height)
        payload = _SIXEL_CACHE.get(key)
        if payload is None:
            scaled = self._image_data.scaled(pixel_width, pixel_height)
            payload = image_to_sixels(scaled.pil_image, self._sixel_options)
            if len(_SIXEL_CACHE) >= _MAX_CACHE_ENTRIES:
                _SIXEL_CACHE.clear()
            _SIXEL_CACHE[key] = payload

        # Same structure as SixelImage.__rich_console__: placeholder lines so
        # Rich knows the size, save the cursor, rewind, sixel, restore.
        for _ in range(cell_height):
            yield Segment(" " * cell_width + "\n")
        yield Segment("\x1b7", control=_NULL_CONTROL)
        yield Control.move(0, -cell_height)
        yield Segment(payload, control=_NULL_CONTROL)
        yield Segment("\x1b8", control=_NULL_CONTROL)


try:  # pragma: no cover - depends on the textual-image internals
    ThumbImage = _CachedSixelImage if AutoImage is SixelImage else AutoImage
except Exception:
    ThumbImage = AutoImage


class CenteredText:
    """One line of text vertically centered in a fixed number of rows."""

    def __init__(self, text, rows: int) -> None:
        self._text = text
        self._rows = rows

    def __str__(self) -> str:
        return str(self._text)

    def __rich_measure__(self, console, options):
        # Report the text's own width. Without this the table measures the
        # multiline cell with a 1-column budget and collapses the column.
        if hasattr(self._text, "__rich_measure__"):
            return self._text.__rich_measure__(console, options)
        return Measurement(1, 1)

    def __repr__(self) -> str:
        return f"CenteredText({self._text!r}, rows={self._rows})"

    def __rich_console__(self, console, options):
        top = (self._rows - 1) // 2
        group = Group(
            *[Text("") for _ in range(top)],
            self._text,
            *[Text("") for _ in range(self._rows - 1 - top)],
        )
        yield from console.render(group, options)


def centered(text, rows: int) -> CenteredText:
    return CenteredText(text, rows)
