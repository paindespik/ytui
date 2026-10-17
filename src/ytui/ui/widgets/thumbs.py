"""Rich renderables for small list-row thumbnails (web-style video lists).

The renderable is auto-selected from ``textual-image`` at import time:
sixel, kitty (TGP), half-block text or unicode blocks, depending on the
terminal.

Memory: upstream renderables keep a decoded PIL copy of the image for their
whole lifetime (~600 KB per 320x180 thumbnail once Pillow's RGBX storage and
allocator overhead are counted). A list keeps one renderable per row, every
screen keeps its list, and each rebuild (feed refresh, watched markers)
creates a fresh set — this used to grow the TUI past 2 GB. The variants
below only read the image header up front, decode on demand and drop the
pixels as soon as the terminal payload is built:

* sixel: the encoded payload is cached per (file, pixel size);
* kitty: the image is transmitted once per (file, pixel size) and its
  terminal id is reused by every row/screen/rebuild that shows it.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from rich.console import Group
from rich.control import Control
from rich.measure import Measurement
from rich.segment import Segment
from rich.text import Text
from textual_image._geometry import ImageSize
from textual_image._pixeldata import PixelData, PixelMeta
from textual_image._terminal import get_cell_size
from textual_image.renderable import Image as AutoImage
from textual_image.renderable import tgp as _tgp
from textual_image.renderable.sixel import Image as SixelImage
from textual_image.renderable.tgp import Image as TGPImage

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

# (file key, pixel w, pixel h) -> kitty image id, in LRU order. kitty keeps
# the pixels (its own quota, 320 MB by default); ~100 KB each at list size.
_TGP_IDS: OrderedDict[tuple, int] = OrderedDict()
_MAX_TGP_IMAGES = 1500


def _file_key(path) -> tuple:
    try:
        mtime = Path(path).stat().st_mtime_ns
    except OSError:
        mtime = -1
    return (str(path), mtime)


class _LazyImage:
    """Common part: size from the image header only, pixels loaded on demand."""

    def __init__(self, path, width=None, height=None) -> None:
        self._path = path
        self._file_key = _file_key(path)
        meta = PixelMeta(path)  # header only, no pixel decoding
        self._render_size = ImageSize(meta.width, meta.height, width, height)

    def _sizes(self, options):
        terminal_sizes = get_cell_size()
        cells = self._render_size.get_cell_size(
            options.max_width, options.max_height, terminal_sizes
        )
        pixels = self._render_size.get_pixel_size(
            options.max_width, options.max_height, terminal_sizes
        )
        return cells, pixels

    def _scaled(self, pixel_width: int, pixel_height: int) -> PixelData:
        return PixelData(self._path).scaled(pixel_width, pixel_height)

    def __rich_measure__(self, console, options):
        (width, _), _ = self._sizes(options)
        return Measurement(width, width)

    def cleanup(self) -> None:
        """Terminal data is shared between renderables: nothing to free."""


class _CachedSixelImage(_LazyImage):
    """Sixel renderable that encodes each image at most once per pixel size."""

    _sixel_options = SixelImage.DEFAULT_OPTIONS

    def __rich_console__(self, console, options):
        from textual_image._sixel import image_to_sixels
        from textual_image.renderable.sixel import _NULL_CONTROL

        (cell_width, cell_height), (pixel_width, pixel_height) = self._sizes(options)
        key = (self._file_key, pixel_width, pixel_height)
        payload = _SIXEL_CACHE.get(key)
        if payload is None:
            scaled = self._scaled(pixel_width, pixel_height)
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


class _CachedTGPImage(_LazyImage):
    """kitty (TGP) renderable transmitting each image once per pixel size."""

    terminal_image_id: int | None = None

    def __rich_console__(self, console, options):
        (cell_width, cell_height), (pixel_width, pixel_height) = self._sizes(options)
        if cell_width > len(_tgp._NUMBER_TO_DIACRITIC) or cell_height > len(
            _tgp._NUMBER_TO_DIACRITIC
        ):
            raise ValueError("Image to large to render")
        key = (self._file_key, pixel_width, pixel_height)
        image_id = _TGP_IDS.get(key)
        if image_id is None:
            image_id = self._transmit(pixel_width, pixel_height, cell_width, cell_height)
            _TGP_IDS[key] = image_id
            while len(_TGP_IDS) > _MAX_TGP_IMAGES:
                _, old_id = _TGP_IDS.popitem(last=False)
                try:
                    _tgp._send_tgp_message(a="d", d="I", i=old_id, q=2)
                except Exception:
                    pass
        else:
            _TGP_IDS.move_to_end(key)
        self.terminal_image_id = image_id
        yield from self._diacritics(image_id, cell_width, cell_height)

    def _transmit(self, pixel_width, pixel_height, cell_width, cell_height) -> int:
        image_id = next(TGPImage._image_id_counter)
        data = self._scaled(pixel_width, pixel_height).to_base64()
        while data:
            chunk, data = data[:4096], data[4096:]
            _tgp._send_tgp_message(i=image_id, m=1 if data else 0, f=100, payload=chunk, q=2)
        # One virtual placement per image id: every row showing this image
        # (same pixel size => same cell size) reuses it.
        _tgp._send_tgp_message(a="p", i=image_id, c=cell_width, r=cell_height, U=1, q=2)
        return image_id

    @staticmethod
    def _diacritics(image_id: int, width: int, height: int):
        holder = TGPImage.__new__(TGPImage)
        holder.terminal_image_id = image_id
        yield from holder._render_diacritics(width, height)


try:  # pragma: no cover - depends on the textual-image internals
    if AutoImage is SixelImage:
        ThumbImage = _CachedSixelImage
    elif AutoImage is TGPImage:
        ThumbImage = _CachedTGPImage
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
