"""Rich renderables for small list-row thumbnails (web-style video lists).

The renderable is auto-selected from ``textual-image`` at import time:
sixel, kitty (TGP), half-block text or unicode blocks, depending on the
terminal. Sixel re-encodes the image on every redraw (rows redraw while
scrolling), so the sixel variant caches the encoded payload per file.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Group
from rich.control import Control
from rich.measure import Measurement
from rich.segment import Segment
from rich.text import Text
from textual_image._terminal import get_cell_size
from textual_image.renderable import Image as AutoImage
from textual_image.renderable.sixel import Image as SixelImage
from textual_image.renderable.tgp import (
    Image as TGPImage,
    _NUMBER_TO_DIACRITIC,
    _send_tgp_message,
)

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


class _ResendTGPImage(TGPImage):
    """TGP (kitty) renderable that places its image at exact cell coordinates.

    The base class sends the image data once per instance and places it at
    the terminal cursor, wherever that happens to be when the cell is first
    rendered (for a table: top-left of the screen, not the cell), and it
    never re-places on later renders, so a row loses its image as soon as
    its cells are re-rendered (cursor row highlight, scroll).

    This subclass re-places the image (``a=p``) on every render at explicit
    screen coordinates provided by the owning table (see ``VideoList``). The
    cursor is saved (DECSC), jumped to the cell, the image placed, and the
    cursor restored (DECRC): the physical cursor never ends up moved, so
    Textual's cursor bookkeeping stays consistent and each render costs
    exactly one short TGP placement message (the image data itself is only
    sent once).
    """

    def __init__(self, image, width=None, height=None) -> None:
        super().__init__(image, width, height)
        # 1-based (screen_row, screen_col) of the cell, or None to place at
        # the current cursor (base behavior).
        self._placement: tuple[int, int] | None = None

    def _place(self, cell_width: int, cell_height: int) -> None:
        """Schedule the placement just after the frame is flushed.

        Emitting during the render phase is useless: Textual writes the
        cell's text to the terminal right afterwards, and the terminal
        deletes an anchored image as soon as its cell is overwritten.
        Deferring the write by a few milliseconds lands it after the
        physical writes of the frame, on top of the (already written)
        cell text. The save/restore cursor pair keeps the physical cursor
        untouched, so this cannot desynchronize Textual's output.
        """
        if self.terminal_image_id is None:
            return
        try:
            import asyncio

            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._emit_place(cell_width, cell_height)
            return
        loop.call_later(0.03, self._emit_place, cell_width, cell_height)

    def _emit_place(self, cell_width: int, cell_height: int) -> None:
        out = sys.__stdout__ if sys.__stdout__ is not None else None
        if out is None or self.terminal_image_id is None:
            return
        try:
            if self._placement is not None:
                row, col = self._placement
                out.write(f"\x1b7\x1b[{row};{col}H")
            _send_tgp_message(
                a="p", i=self.terminal_image_id, c=cell_width, r=cell_height, U=1, q=2
            )
            if self._placement is not None:
                out.write("\x1b8")
            out.flush()
        except Exception:
            pass

    def __rich_console__(self, console, options):
        terminal_sizes = get_cell_size()
        cell_width, cell_height = self._render_size.get_cell_size(
            options.max_width, options.max_height, terminal_sizes
        )
        pixel_width, pixel_height = self._render_size.get_pixel_size(
            options.max_width, options.max_height, terminal_sizes
        )
        if cell_width > len(_NUMBER_TO_DIACRITIC) or cell_height > len(_NUMBER_TO_DIACRITIC):
            raise ValueError("Image too large to render")
        if self.terminal_image_id is None:
            self._send_image_to_terminal(pixel_width, pixel_height)
        self._place(cell_width, cell_height)
        yield from self._render_diacritics(cell_width, cell_height)


try:  # pragma: no cover - depends on the textual-image internals
    if AutoImage is SixelImage:
        ThumbImage = _CachedSixelImage
    elif AutoImage is TGPImage:
        ThumbImage = _ResendTGPImage
    else:
        ThumbImage = AutoImage
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
