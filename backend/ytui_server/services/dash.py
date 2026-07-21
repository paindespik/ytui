"""Static DASH manifest generation for split MP4 (avc1 + m4a) streams.

The MPD points dash.js at two proxied BaseURLs with SegmentBase indexRange
(Invidious-style architecture): the browser fetches init + sidx byte ranges
itself, no server-side segmenting.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable

import httpx

from .ytdlp import SplitMp4

# First 128 KiB is more than enough to cover ftyp+moov+sidx in YouTube DASH files.
_PROBE_RANGE = "bytes=0-131071"


def find_sidx(data: bytes) -> tuple[int, int] | None:
    """Walk top-level ISO-BMFF boxes and return (offset, size) of the sidx box.

    Box header: uint32 BE size + fourcc; size == 1 means a 64-bit largesize
    follows; size == 0 (box extends to EOF) or a size smaller than its own
    header is treated as end of parseable data.
    """
    offset = 0
    end = len(data)
    while offset + 8 <= end:
        size = int.from_bytes(data[offset : offset + 4], "big")
        fourcc = data[offset + 4 : offset + 8]
        header = 8
        if size == 1:
            if offset + 16 > end:
                return None
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
            header = 16
        if size < header:  # size == 0 (to-EOF) or corrupt header
            return None
        if fourcc == b"sidx":
            return offset, size
        offset += size
    return None


async def probe_ranges(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None:
    """Fetch the file head and derive (init_range, index_range) from the sidx box."""
    try:
        resp = await client.get(url, headers={"Range": _PROBE_RANGE})
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None
    found = find_sidx(resp.content)
    if found is None:
        return None
    offset, size = found
    return f"0-{offset - 1}", f"{offset}-{offset + size - 1}"


def _add_representation(
    adaptation: ET.Element, attrs: dict[str, str], base_url: str, ranges: tuple[str, str]
) -> None:
    rep = ET.SubElement(adaptation, "Representation", attrs)
    ET.SubElement(rep, "BaseURL").text = base_url
    init_range, index_range = ranges
    segment_base = ET.SubElement(rep, "SegmentBase", {"indexRange": index_range})
    ET.SubElement(segment_base, "Initialization", {"range": init_range})


def build_mpd(
    split: SplitMp4,
    proxy: Callable[[str], str],
    video_ranges: tuple[str, str],
    audio_ranges: tuple[str, str],
) -> str:
    """Build a static on-demand MPD with one avc1 and one mp4a Representation."""
    mpd = ET.Element(
        "MPD",
        {
            "xmlns": "urn:mpeg:dash:schema:mpd:2011",
            "type": "static",
            "profiles": "urn:mpeg:dash:profile:isoff-on-demand:2011",
            "minBufferTime": "PT2S",
            "mediaPresentationDuration": f"PT{split.duration}S",
        },
    )
    period = ET.SubElement(mpd, "Period")

    video_set = ET.SubElement(period, "AdaptationSet", {"mimeType": "video/mp4"})
    _add_representation(
        video_set,
        {
            "id": "video",
            "codecs": split.video_codec,
            "bandwidth": str(split.video_bitrate),
            "width": str(split.width),
            "height": str(split.height),
        },
        proxy(split.video_url),
        video_ranges,
    )

    audio_set = ET.SubElement(period, "AdaptationSet", {"mimeType": "audio/mp4"})
    _add_representation(
        audio_set,
        {
            "id": "audio",
            "codecs": split.audio_codec,
            "bandwidth": str(split.audio_bitrate),
        },
        proxy(split.audio_url),
        audio_ranges,
    )

    return ET.tostring(mpd, encoding="unicode", xml_declaration=True)
