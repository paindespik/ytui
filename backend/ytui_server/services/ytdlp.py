"""yt-dlp based search, listings, details and stream URL resolution.

All yt-dlp calls are blocking: they are wrapped with anyio.to_thread and a
global semaphore to bound server load.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

import anyio
import httpx

from ..models import StreamInfo, SubtitleTrackOut, Video, VideoDetails

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ytui-server/0.2"
# Only for the signed-in home feed: YouTube tailors that grid to the client.
_BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0"
# Cookies without which youtube.com serves the anonymous home (see _save_cookies).
_SESSION_COOKIES = ("LOGIN_INFO", "SAPISID", "__Secure-3PAPISID", "__Secure-1PAPISID")
# Home-feed pagination: each InnerTube page carries ~24 videos.
_HOME_MAX_PAGES = 12

log = logging.getLogger(__name__)
_YT_INITIAL_DATA_RE = re.compile(r"ytInitialData\s*=\s*(\{.*?\});</script>", re.DOTALL)

_YDL_FLAT_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,
    "skip_download": True,
    "socket_timeout": 15,
}

_PLAYLIST_URL_RE = re.compile(r"[?&]list=([\w-]+)")
_CHANNEL_URL_RE = re.compile(r"youtube\.com/(?:channel/|c/|user/|@)")
_ODYSEE_PATH_RE = re.compile(r"odysee\.com/(?:@[^/]+(?::\w+)?/)?([^/?#]+:[0-9a-f]+)")
_LBRY_URI_RE = re.compile(r"lbry://(?:@[^/]+(?:[:#]\w+)?/)?([^/?#:]+)[:#]([0-9a-f]+)")

_INTERACTIVE_SEM = asyncio.Semaphore(3)  # streams/details: a user is waiting
_BULK_SEM = asyncio.Semaphore(4)         # search/listings/suggestions


# BitChute serves every file from one of many "seedNNN" hosts. yt-dlp probes a
# hardcoded host list and keeps the first HEAD that does not raise; a flaky HEAD
# on the canonical host (RemoteDisconnected, seen in production) makes it fall
# through to a host that answers 200 with a Cloudflare HTML page, and the player
# is then handed a text/html "video". yt-dlp's probing is therefore turned off
# (check_formats) and the URL validated here, on content type.
_BITCHUTE_SEED_RE = re.compile(r"^(https?://)seed[\w-]+(\.bitchute\.com/)")
_BITCHUTE_SEEDS = (
    "seed122", "seed125", "seed126", "seed128", "seed132", "seed150", "seed151",
    "seed152", "seed153", "seed167", "seed171", "seed177", "seed305", "seed307",
    "seedp29xb", "zb10-7gsop1v78",
)
_MEDIA_CONTENT_TYPES = ("video/", "audio/", "application/octet-stream")


def _serves_media(client: httpx.Client, url: str, attempts: int) -> bool:
    """True when `url` answers a HEAD with a media content type."""
    for _ in range(attempts):
        try:
            resp = client.head(url)
        except httpx.HTTPError as exc:
            log.debug("bitchute HEAD failed for %s: %s", url, exc)
            continue
        if resp.status_code >= 400:
            return False
        ctype = (resp.headers.get("content-type") or "").lower()
        return ctype.startswith(_MEDIA_CONTENT_TYPES)
    return False


def _bitchute_media_url(url: str) -> str:
    """The first BitChute seed host actually serving the file behind `url`.

    The canonical host gets a second try (its HEADs drop connections at random);
    the alternates only one. Nothing playable found: keep the canonical URL.
    """
    if ".m3u8" in url or not _BITCHUTE_SEED_RE.match(url):
        return url
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        if _serves_media(client, url, attempts=2):
            return url
        for host in _BITCHUTE_SEEDS:
            alt = _BITCHUTE_SEED_RE.sub(rf"\g<1>{host}\g<2>", url)
            if alt != url and _serves_media(client, alt, attempts=1):
                log.info("bitchute: %s unusable, serving from %s", url, host)
                return alt
    log.warning("bitchute: no seed host serves %s", url)
    return url


def _fix_bitchute_formats(info: dict) -> None:
    """Point every BitChute format at a host that really serves the file."""
    resolved: dict[str, str] = {}
    for fmt in [*(info.get("formats") or []), info]:
        src = fmt.get("url")
        if not src:
            continue
        fixed = resolved.get(src)
        if fixed is None:
            fixed = resolved[src] = _bitchute_media_url(src)
        fmt["url"] = fixed


# YouTube enrols a share of anonymous playback sessions in an A/B bucket whose
# stream URLs only serve the first ~60 s of media and answer 403 past that
# point (that client is expected to carry a PO token, which we cannot mint).
# Nothing in the URL is reliably distinguishable — the tell is an experiment id
# in `fexp` that YouTube is free to renumber — so the bucket is detected by
# asking for the last byte of the file. Re-extracting re-rolls the bucket.
_TRUNCATION_PROBE_TIMEOUT = 8.0
# Bucket assignment is random per extraction but clusters in time: ~1 resolution
# in 3 comes back capped, so a couple of spare attempts are worth the seconds.
_TRUNCATION_ATTEMPTS = 4
_GOOGLEVIDEO_RE = re.compile(r"^https://[\w.-]*googlevideo\.com/videoplayback\b")
# A live has no `videoplayback` URL to range-probe: it is served as HLS, whose
# per-variant media playlists live under manifest.googlevideo.com. The same
# bucket poisons those too — every segment of every variant answers 403 — so
# lives need their own probe or they slip through the guard entirely.
_HLS_PLAYLIST_RE = re.compile(
    r"^https://[\w.-]*googlevideo\.com/api/manifest/hls_playlist/"
)


def _truncation_probe_target(formats: list[dict]) -> tuple[int, str] | None:
    """Smallest googlevideo format that advertises its length, as (clen, url)."""
    best: tuple[int, str] | None = None
    for fmt in formats:
        url = fmt.get("url") or ""
        if not _GOOGLEVIDEO_RE.match(url):
            continue
        clen = (parse_qs(urlparse(url).query).get("clen") or [""])[0]
        if not clen.isdigit() or int(clen) < 2:
            continue
        if best is None or int(clen) < best[0]:
            best = (int(clen), url)
    return best


def _hls_probe_target(formats: list[dict]) -> str | None:
    """Cheapest googlevideo HLS media playlist to sample a segment from.

    Picking the lowest-bitrate variant keeps the probe cheap; the bucket poisons
    every variant of an extraction alike, so any one of them is a valid tell.
    """
    best: tuple[float, str] | None = None
    for fmt in formats:
        url = fmt.get("url") or ""
        if not _HLS_PLAYLIST_RE.match(url):
            continue
        weight = float(fmt.get("tbr") or fmt.get("height") or 0)
        if best is None or weight < best[0]:
            best = (weight, url)
    return None if best is None else best[1]


def _hls_segment_forbidden(client: httpx.Client, playlist_url: str) -> bool:
    """True when the first segment of an HLS media playlist answers 403.

    A live has no end of file to range-probe, so the tell is the segments
    themselves: in the capped bucket every one of them is refused, which strands
    the player on a frozen picture instead of failing outright.
    """
    resp = client.get(playlist_url)
    if resp.status_code == 403:
        return True
    if resp.status_code >= 400:  # anything else: not our bucket to diagnose
        return False
    segment = next(
        (
            line.strip()
            for line in resp.text.splitlines()
            if line.strip() and not line.startswith("#")
        ),
        None,
    )
    if segment is None:
        return False
    return client.get(segment, headers={"Range": "bytes=0-1"}).status_code == 403


def _delivery_truncated(info: dict) -> bool:
    """True when the resolved URLs stop serving bytes partway through the file.

    A single-byte range request at EOF costs nothing and is unambiguous: the
    capped bucket answers 403 there while a healthy URL answers 206. Lives carry
    no such file, so they are probed one level deeper, through their playlist.
    """
    formats = info.get("formats") or []
    target = _truncation_probe_target(formats)
    playlist = _hls_probe_target(formats) if target is None else None
    url = target[1] if target is not None else playlist
    if url is None:
        return False
    try:
        with httpx.Client(timeout=_TRUNCATION_PROBE_TIMEOUT) as client:
            if target is None:
                return _hls_segment_forbidden(client, url)
            clen = target[0]
            resp = client.get(url, headers={"Range": f"bytes={clen - 1}-{clen - 1}"})
    except httpx.HTTPError as exc:  # network hiccup: assume the stream is fine
        log.debug("truncation probe failed for %s: %s", url, exc)
        return False
    return resp.status_code == 403


# TTL cache for full (non-flat) extract_info results. The web player hits
# /streams then /mpd for the same video back to back; without this, each
# request pays a full multi-second yt-dlp extraction.
_INFO_CACHE: dict[str, tuple[float, dict]] = {}
_INFO_CACHE_LOCK = threading.Lock()
_INFO_CACHE_TTL = 300.0  # seconds; stream URLs themselves last ~6 h
# A live is the exception: in the capped bucket its segment URLs start answering
# 403 about a minute after extraction (measured: 200 at +26 s, 403 from +51 s on).
# Caching those for five minutes would hand the same dead URLs back to a client
# that is retrying precisely because they died — re-extracting is the only way
# out, and it re-rolls the bucket, so keep live entries barely long enough to
# serve the /streams + /mpd burst of a single page load.
_INFO_CACHE_TTL_LIVE = 20.0
_INFO_CACHE_MAX = 32


def _info_cache_ttl(info: dict) -> float:
    return _INFO_CACHE_TTL_LIVE if info.get("is_live") else _INFO_CACHE_TTL


def _extract_info_cached(url: str) -> dict:
    """extract_info with a small module-level TTL cache (thread-safe)."""
    now = time.monotonic()
    with _INFO_CACHE_LOCK:
        cached = _INFO_CACHE.get(url)
        if cached is not None and now - cached[0] < _info_cache_ttl(cached[1]):
            return cached[1]
    import yt_dlp

    bitchute = "bitchute.com" in url
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 15}
    if bitchute:
        opts["check_formats"] = False  # see _bitchute_media_url
    info = {}
    for attempt in range(1, _TRUNCATION_ATTEMPTS + 1):
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if attempt == _TRUNCATION_ATTEMPTS or not _delivery_truncated(info):
            break
        log.warning(
            "youtube served ~60 s-capped stream URLs for %s, re-resolving (%d/%d)",
            url,
            attempt,
            _TRUNCATION_ATTEMPTS,
        )
    if bitchute:
        _fix_bitchute_formats(info)
    with _INFO_CACHE_LOCK:
        if url not in _INFO_CACHE and len(_INFO_CACHE) >= _INFO_CACHE_MAX:
            oldest = min(_INFO_CACHE, key=lambda k: _INFO_CACHE[k][0])
            del _INFO_CACHE[oldest]
        _INFO_CACHE[url] = (time.monotonic(), info)
    return info


class UpstreamError(Exception):
    """yt-dlp extraction failed."""


def entry_to_item(entry: dict) -> Video | None:
    """Convert a flat yt-dlp entry (video, playlist or channel) into a Video item."""
    entry_id = entry.get("id")
    if not entry_id:
        return None
    url = entry.get("url") or entry.get("webpage_url") or ""
    if "odysee.com" in url or url.startswith("lbry://"):
        platform = "odysee"
        # yt-dlp reports the bare claim_id as id; prefer "name:claim_id" from the URL
        m = _ODYSEE_PATH_RE.search(url)
        if m:
            entry_id = m.group(1)
        else:
            m = _LBRY_URI_RE.search(url)
            if m:
                entry_id = f"{m.group(1)}:{m.group(2)}"
    elif "bitchute.com" in url:
        platform = "bitchute"
    elif "twitch.tv/" in url:
        platform = "twitch"
    else:
        platform = "youtube"
    kind = "video"
    if entry.get("ie_key") == "YoutubeTab" or entry.get("_type") == "playlist":
        if _PLAYLIST_URL_RE.search(url) or entry_id.startswith(("PL", "RD", "OL", "UU", "LL")):
            kind = "playlist"
        elif _CHANNEL_URL_RE.search(url) or entry_id.startswith("UC"):
            kind = "channel"
        else:
            kind = "playlist"
    published = None
    ts = entry.get("timestamp") or entry.get("release_timestamp")
    if ts:
        published = datetime.fromtimestamp(ts, tz=timezone.utc)
    duration = entry.get("duration")
    thumb = ""
    if kind == "video" and platform == "youtube":
        thumb = f"https://i.ytimg.com/vi/{entry_id}/mqdefault.jpg"
    else:
        thumbs = entry.get("thumbnails") or []
        if thumbs:
            thumb = thumbs[-1].get("url") or ""
        thumb = thumb or entry.get("thumbnail") or ""
    return Video(
        video_id=entry_id,
        title=entry.get("title") or "",
        channel_title=entry.get("channel") or entry.get("uploader") or "",
        channel_id=entry.get("channel_id") or "",
        published=published,
        duration=int(duration) if duration else None,
        thumbnail_url=thumb,
        kind=kind,
        platform=platform,
    )


def _extract_flat(url: str, limit: int | None = None, offset: int = 0) -> list[Video]:
    import yt_dlp

    opts = dict(_YDL_FLAT_OPTS)
    if limit:
        opts["playlist_items"] = f"{offset + 1}:{offset + limit}"
    elif offset:
        opts["playlist_items"] = f"{offset + 1}:"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    items: list[Video] = []
    for entry in info.get("entries") or []:
        item = entry_to_item(entry)
        if item:
            items.append(item)
    return items


def _extract_flat_info(url: str, limit: int | None = None) -> tuple[list[Video], str]:
    """Like _extract_flat but also returns the container title (playlist name)."""
    import yt_dlp

    opts = dict(_YDL_FLAT_OPTS)
    if limit:
        opts["playlist_items"] = f"1:{limit}"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    items: list[Video] = []
    for entry in info.get("entries") or []:
        item = entry_to_item(entry)
        if item:
            items.append(item)
    return items, info.get("title") or ""


async def _run(sem: asyncio.Semaphore, func, *args):
    async with sem:
        try:
            return await anyio.to_thread.run_sync(func, *args)
        except Exception as exc:
            log.warning("yt-dlp %s failed: %s", func.__name__, exc)
            raise UpstreamError(str(exc)) from exc


async def search_videos(query: str, limit: int = 20) -> list[Video]:
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    return await _run(_BULK_SEM, _extract_flat, url, limit)


async def channel_videos(channel_url: str, limit: int = 50, offset: int = 0) -> list[Video]:
    url = channel_url.rstrip("/")
    if (
        "bitchute.com" not in url
        and "odysee.com" not in url
        and not url.endswith("/videos")
    ):
        url += "/videos"
    return await _run(_BULK_SEM, _extract_flat, url, limit, offset)


async def channel_search(
    channel_url: str, query: str, limit: int = 50, offset: int = 0
) -> list[Video]:
    """Videos of a YouTube channel matching `query` (the channel's Search tab).

    Only YouTube serves this tab; callers must not route other platforms here.
    """
    url = f"{channel_url.rstrip('/')}/search?query={quote_plus(query)}"
    return await _run(_BULK_SEM, _extract_flat, url, limit, offset)


async def playlist_videos(playlist_url: str, limit: int = 200) -> tuple[list[Video], str]:
    return await _run(_BULK_SEM, _extract_flat_info, playlist_url, limit)


def _extract_details(url: str) -> VideoDetails:
    import yt_dlp

    # check_formats: /details never serves a stream URL, so BitChute's seed
    # probing would only cost a pointless HEAD round.
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 15,
        "check_formats": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    upload_date = info.get("upload_date") or ""
    if len(upload_date) == 8:
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    duration = info.get("duration")
    return VideoDetails(
        video_id=info.get("id") or "",
        title=info.get("title") or "",
        channel_id=info.get("channel_id") or "",
        channel_title=info.get("channel") or info.get("uploader") or "",
        description=info.get("description") or "",
        view_count=info.get("view_count"),
        like_count=info.get("like_count"),
        duration=int(duration) if duration else None,
        upload_date=upload_date,
    )


async def video_details(url: str) -> VideoDetails:
    return await _run(_INTERACTIVE_SEM, _extract_details, url)


def _stream_expiry(url: str) -> datetime | None:
    """googlevideo URLs carry an 'expire' unix timestamp query param."""
    try:
        qs = parse_qs(urlparse(url).query)
        expire = qs.get("expire", [None])[0]
        if expire:
            return datetime.fromtimestamp(int(expire), tz=timezone.utc)
    except (ValueError, TypeError):
        pass
    return None


def _capped(fmts: list[dict], max_height: int) -> list[dict]:
    """Formats at or below `max_height`; when none fit, the lowest available.

    A cap must never make a video unplayable: a 1080p-only source served to a
    720p cap is degraded gracefully (clients report the served height).
    """
    fitting = [f for f in fmts if (f.get("height") or 0) <= max_height]
    if fitting:
        return fitting
    if not fmts:
        return []
    lowest = min((f.get("height") or 0) for f in fmts)
    return [f for f in fmts if (f.get("height") or 0) == lowest]


def _extract_streams(
    url: str, max_height: int, audio_only: bool, sub_langs: str
) -> StreamInfo:
    info = _extract_info_cached(url)

    title = info.get("title") or ""
    duration = info.get("duration")
    duration = int(duration) if duration else None
    formats = info.get("formats") or []

    def _pick_sub_url(fmts: list[dict]) -> str | None:
        for f in fmts:
            if f.get("ext") == "vtt":
                return f.get("url")
        return fmts[0].get("url") if fmts else None

    subtitles: list[SubtitleTrackOut] = []
    if not audio_only:
        for lang, fmts in (info.get("subtitles") or {}).items():
            if lang.startswith("live_chat"):
                continue  # chat replay JSON, not a subtitle track
            sub_url = _pick_sub_url(fmts)
            if sub_url:
                subtitles.append(
                    SubtitleTrackOut(
                        lang=lang, label=(fmts[0].get("name") or lang), url=sub_url
                    )
                )
        # Manual subs: every language (short list). Auto captions: only the
        # requested languages, else hundreds of translated variants show up.
        wanted = [s.strip() for s in sub_langs.split(",") if s.strip()]
        manual = {s.lang for s in subtitles}
        for lang in wanted:
            if lang in manual:
                continue
            sub_url = _pick_sub_url((info.get("automatic_captions") or {}).get(lang) or [])
            if sub_url:
                subtitles.append(
                    SubtitleTrackOut(
                        lang=lang, label=f"{lang} (auto)", url=sub_url, auto=True
                    )
                )

    def make(kind, url_, video_url=None, audio_url=None, height=None):
        return StreamInfo(
            kind=kind,
            url=url_,
            video_url=video_url,
            audio_url=audio_url,
            title=title,
            duration=duration,
            expires_at=_stream_expiry(url_),
            height=height,
            is_live=bool(info.get("is_live")),
            subtitles=subtitles,
        )

    if audio_only:
        audio = [
            f
            for f in formats
            if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
        ]
        if audio:
            best = max(audio, key=lambda f: f.get("abr") or f.get("tbr") or 0)
            return make("progressive", best["url"])
        # fall through: use whatever combined format exists

    # 1. HLS manifest (adaptive; best for mobile players)
    hls_all = [
        f
        for f in formats
        if f.get("protocol", "").startswith("m3u8")
        and f.get("vcodec") not in (None, "none")
    ]
    if hls_all:
        hls = _capped(hls_all, max_height)
        best = max(hls, key=lambda f: f.get("height") or 0)
        height = best.get("height") or None
        top = max((f.get("height") or 0) for f in hls_all)
        # The master manifest lets the player adapt, but it carries every
        # variant: only serve it when the cap took nothing away.
        if top > (best.get("height") or 0):
            return make("hls", best["url"], height=height)
        manifest = info.get("manifest_url") or ""
        return make("hls", manifest or best["url"], height=height)

    # 2/3. Progressive (one muxed file) vs split DASH (video-only + audio-only).
    # Order must not decide: YouTube only muxes up to 360p, so taking the first
    # progressive hit caps every VOD at 360p however high the client asked. Pick
    # whichever reaches higher, progressive on a tie — one file needs no external
    # audio track to stay in sync, and it keeps the web player's 360p fallback
    # (max_height=360) on the single-file path.
    muxed_all = [
        f
        for f in formats
        if f.get("acodec") not in (None, "none") and f.get("vcodec") not in (None, "none")
    ]
    video_all = [
        f
        for f in formats
        if f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none")
    ]
    audios = [
        f
        for f in formats
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    if not audios:
        video_all = []  # video-only formats are unplayable without an audio track

    progressive = [f for f in muxed_all if (f.get("height") or 0) <= max_height]
    videos = [f for f in video_all if (f.get("height") or 0) <= max_height]
    if not progressive and not videos:
        # Nothing fits: degrade to the lowest available rather than fail, but only
        # once neither path can honour the cap (_capped applied per path would let
        # an over-cap split outrank a compliant progressive).
        progressive = _capped(muxed_all, max_height)
        videos = _capped(video_all, max_height)

    best_prog = (
        max(progressive, key=lambda f: f.get("height") or 0) if progressive else None
    )
    bv = (
        max(videos, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
        if videos
        else None
    )

    # -1 keeps an absent candidate below a real 0-height one (unknown height).
    prog_height = (best_prog.get("height") or 0) if best_prog else -1
    split_height = (bv.get("height") or 0) if bv else -1

    if bv is not None and split_height > prog_height:
        ba = max(audios, key=lambda f: f.get("abr") or f.get("tbr") or 0)
        return make(
            "split",
            bv["url"],
            video_url=bv["url"],
            audio_url=ba["url"],
            height=bv.get("height") or None,
        )
    if best_prog is not None:
        return make(
            "progressive", best_prog["url"], height=best_prog.get("height") or None
        )

    # 4. Last resort: top-level url
    if info.get("url"):
        return make("progressive", info["url"])
    raise UpstreamError("No playable stream found")


async def resolve_streams(
    url: str, max_height: int = 1440, audio_only: bool = False, sub_langs: str = ""
) -> StreamInfo:
    return await _run(
        _INTERACTIVE_SEM, _extract_streams, url, max_height, audio_only, sub_langs
    )


@dataclass
class SplitMp4:
    """Best avc1 video-only + mp4a audio-only pair for DASH MPD generation."""

    video_url: str
    width: int
    height: int
    video_codec: str
    video_bitrate: int  # bps
    audio_url: str
    audio_codec: str
    audio_bitrate: int  # bps
    duration: int  # seconds


def _extract_split_mp4(url: str, max_height: int) -> SplitMp4 | None:
    # avc1/mp4a only: one MP4 sidx parser, universal browser support.
    info = _extract_info_cached(url)
    formats = info.get("formats") or []
    videos = [
        f
        for f in formats
        if (f.get("vcodec") or "").startswith("avc1")
        and f.get("acodec") in (None, "none")
        and f.get("ext") == "mp4"
        and not (f.get("protocol") or "").startswith("m3u8")
    ]
    videos = _capped(videos, max_height)
    audios = [
        f
        for f in formats
        if (f.get("acodec") or "").startswith("mp4a")
        and f.get("vcodec") in (None, "none")
        and f.get("ext") in ("m4a", "mp4")
    ]
    if not videos or not audios:
        return None
    bv = max(videos, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
    ba = max(audios, key=lambda f: f.get("abr") or f.get("tbr") or 0)
    height = bv.get("height") or 0
    duration = info.get("duration")
    return SplitMp4(
        video_url=bv["url"],
        width=bv.get("width") or height * 16 // 9,
        height=height,
        video_codec=bv.get("vcodec") or "",
        video_bitrate=int((bv.get("tbr") or 0) * 1000) or 2_000_000,
        audio_url=ba["url"],
        audio_codec=ba.get("acodec") or "",
        audio_bitrate=int((ba.get("abr") or ba.get("tbr") or 0) * 1000) or 128_000,
        duration=int(duration) if duration else 0,
    )


async def resolve_split_mp4(url: str, max_height: int) -> SplitMp4 | None:
    return await _run(_INTERACTIVE_SEM, _extract_split_mp4, url, max_height)


def _parse_badge_duration(text: str) -> int | None:
    """Parse a duration badge like '9:26' or '1:09:26' into seconds."""
    parts = text.strip().split(":")
    if not all(p.isdigit() for p in parts) or not 2 <= len(parts) <= 3:
        return None
    parts_i = [int(p) for p in parts]
    seconds = 0
    for p in parts_i:
        seconds = seconds * 60 + p
    return seconds


def _walk_lockups(node: object) -> list[dict]:
    """Recursively collect every lockupViewModel dict with contentType == video."""
    found: list[dict] = []
    if isinstance(node, dict):
        lockup = node.get("lockupViewModel")
        if isinstance(lockup, dict) and lockup.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
            found.append(lockup)
        for value in node.values():
            found.extend(_walk_lockups(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_lockups(item))
    return found


def _find_browse_id(node: object) -> str:
    """First browseEndpoint channel id (UC…) found anywhere under `node`."""
    if isinstance(node, dict):
        endpoint = node.get("browseEndpoint")
        if isinstance(endpoint, dict):
            browse_id = endpoint.get("browseId")
            if isinstance(browse_id, str) and browse_id.startswith("UC"):
                return browse_id
        for value in node.values():
            found = _find_browse_id(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_browse_id(item)
            if found:
                return found
    return ""


def _lockup_to_video(lockup: dict) -> Video | None:
    content_id = lockup.get("contentId") or ""
    if not content_id:
        return None
    metadata_vm = (lockup.get("metadata") or {}).get("lockupMetadataViewModel") or {}
    title = ((metadata_vm.get("title") or {}).get("content")) or ""
    if not title:
        return None

    channel_title = ""
    content_metadata = (metadata_vm.get("metadata") or {}).get("contentMetadataViewModel") or {}
    rows = content_metadata.get("metadataRows") or []
    if rows:
        parts = rows[0].get("metadataParts") or []
        if parts:
            channel_title = ((parts[0].get("text") or {}).get("content")) or ""

    thumbnail_url = ""
    image = ((lockup.get("contentImage") or {}).get("thumbnailViewModel") or {}).get("image") or {}
    sources = image.get("sources") or []
    if sources:
        best = max(sources, key=lambda s: s.get("width") or 0)
        thumbnail_url = best.get("url") or ""

    duration = None
    overlay = (
        (lockup.get("contentImage") or {}).get("thumbnailViewModel") or {}
    ).get("overlays") or []
    for entry in overlay:
        bottom = (entry.get("thumbnailOverlayBadgeViewModel") or {}).get("thumbnailBadges") or []
        for badge in bottom:
            text = (
                (badge.get("thumbnailBadgeViewModel") or {}).get("text")
            ) or ""
            parsed = _parse_badge_duration(text) if text else None
            if parsed is not None:
                duration = parsed
                break
        if duration is not None:
            break
    if duration is None:
        overlay_direct = (lockup.get("contentImage") or {}).get(
            "thumbnailBottomOverlayViewModel"
        ) or {}
        badges = overlay_direct.get("thumbnailBadges") or []
        for badge in badges:
            text = ((badge.get("thumbnailBadgeViewModel") or {}).get("text")) or ""
            parsed = _parse_badge_duration(text) if text else None
            if parsed is not None:
                duration = parsed
                break

    return Video(
        video_id=content_id,
        title=title,
        channel_title=channel_title,
        channel_id=_find_browse_id(metadata_vm),
        thumbnail_url=thumbnail_url,
        duration=duration,
        kind="video",
        platform="youtube",
    )


async def related_videos(video_id: str, limit: int = 20) -> list[Video]:
    """Scrape YouTube's watch page ytInitialData for related/suggested videos."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US"}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as http_client:
            resp = await http_client.get(url, headers=headers)
            if resp.status_code == 429:
                raise UpstreamError("YouTube rate-limited related videos (429)")
            resp.raise_for_status()
            html = resp.text
        match = _YT_INITIAL_DATA_RE.search(html)
        if not match:
            raise UpstreamError("ytInitialData not found")
        data = json.loads(match.group(1))
    except UpstreamError:
        raise
    except Exception as exc:
        log.warning("yt-dlp related_videos failed: %s", exc)
        raise UpstreamError(str(exc)) from exc

    videos: list[Video] = []
    seen: set[str] = set()
    for lockup in _walk_lockups(data):
        try:
            video = _lockup_to_video(lockup)
        except Exception:
            continue
        if video is None or video.video_id in seen:
            continue
        seen.add(video.video_id)
        videos.append(video)
        if len(videos) >= limit:
            break
    return videos


async def home_recommendations(cookie_file: Path, limit: int = 50) -> list[Video]:
    """The account's YouTube home feed, scraped with the pushed cookies.

    Reads the very page the browser shows, so the content and its order match
    what the account sees on youtube.com. yt-dlp's `:ytrec`
    (/feed/recommended) was measured to carry the same videos but also mixes in
    radio playlists ("My Mix") in a different order, which made the tab look
    nothing like the real home.

    The HTML grid only carries ~26 videos; past that the browser scrolls by
    calling InnerTube. We do the same (see _home_continuations), which is what
    lets `limit` go well beyond the first screenful.

    Returns [] when the cookies no longer authenticate, so callers can warn and
    fall back instead of serving an anonymous feed as if it were personal.
    """
    from yt_dlp.cookies import YoutubeDLCookieJar

    jar = YoutubeDLCookieJar(str(cookie_file))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        raise UpstreamError(f"unreadable cookie file: {exc}") from exc
    credentials = {c.name for c in jar} & set(_SESSION_COOKIES)

    videos: list[Video] = []
    seen: set[str] = set()
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            cookies=jar,
            # A browser User-Agent is not cosmetic here: with the server's own
            # UA YouTube returns a thinner grid (measured 18 items vs 23).
            headers={"User-Agent": _BROWSER_UA},
        ) as http_client:
            resp = await http_client.get("https://www.youtube.com/")
            if resp.status_code == 429:
                raise UpstreamError("YouTube rate-limited the home feed (429)")
            resp.raise_for_status()
            html = resp.text
            if '"LOGGED_IN":true' not in html:
                log.warning("home feed served anonymously: the cookies no longer authenticate")
                return []
            match = _YT_INITIAL_DATA_RE.search(html)
            if not match:
                raise UpstreamError("ytInitialData not found")
            data = json.loads(match.group(1))

            # _walk_lockups keeps only LOCKUP_CONTENT_TYPE_VIDEO, which drops
            # the ad slots, the Shorts shelf and the radio playlists.
            _collect_lockups(data, videos, seen, limit)
            if len(videos) < limit:
                await _home_continuations(http_client, jar, html, videos, seen, limit)
    except UpstreamError:
        raise
    except Exception as exc:
        log.warning("home recommendations failed: %s", exc)
        raise UpstreamError(str(exc)) from exc

    # Google rotates session cookies on every request; persisting them is what
    # keeps the pushed session from lapsing.
    _save_cookies(jar, cookie_file, credentials)
    return videos


def _collect_lockups(
    node: object, videos: list[Video], seen: set[str], limit: int
) -> None:
    """Append every new video lockup found in `node`, up to `limit` in total."""
    for lockup in _walk_lockups(node):
        if len(videos) >= limit:
            return
        try:
            video = _lockup_to_video(lockup)
        except Exception:
            continue
        if video is None or video.video_id in seen:
            continue
        seen.add(video.video_id)
        videos.append(video)


def _sapisid_hash(jar, origin: str = "https://www.youtube.com") -> str | None:
    """The Authorization header the web client signs InnerTube calls with."""
    values = {c.name: c.value for c in jar}
    sapisid = values.get("SAPISID") or values.get("__Secure-3PAPISID")
    if not sapisid:
        return None
    now = int(time.time())
    digest = hashlib.sha1(f"{now} {sapisid} {origin}".encode()).hexdigest()
    return f"SAPISIDHASH {now}_{digest}"


def _json_object_after(text: str, marker: str) -> dict:
    """The brace-balanced JSON object that follows `marker` in `text`."""
    start = text.index(marker) + len(marker)
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError(f"unbalanced JSON after {marker}")


async def _home_continuations(
    http_client: httpx.AsyncClient,
    jar,
    html: str,
    videos: list[Video],
    seen: set[str],
    limit: int,
) -> None:
    """Page the home grid through InnerTube, the way scrolling does.

    The continuation token embedded in the HTML is rejected ("Votre flux
    d'accueil a expiré"), so the grid is re-requested through browse
    FEwhat_to_watch — its tokens do work — and followed page by page. Failures
    are swallowed: the HTML page already gave us a usable feed.
    """
    authorization = _sapisid_hash(jar)
    if authorization is None:
        return
    try:
        context = _json_object_after(html, '"INNERTUBE_CONTEXT":')
    except (ValueError, KeyError):
        log.warning("home feed: INNERTUBE_CONTEXT not found, no pagination")
        return
    version = context.get("client", {}).get("clientVersion", "")
    headers = {
        "Content-Type": "application/json",
        "X-Youtube-Client-Name": "1",
        "X-Youtube-Client-Version": version,
        "Authorization": authorization,
        "X-Origin": "https://www.youtube.com",
        "X-Goog-AuthUser": "0",
        "Origin": "https://www.youtube.com",
        "Referer": "https://www.youtube.com/",
    }
    url = "https://www.youtube.com/youtubei/v1/browse?prettyPrint=false"
    body: dict = {"context": context, "browseId": "FEwhat_to_watch"}
    for _ in range(_HOME_MAX_PAGES):
        try:
            resp = await http_client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("home feed pagination stopped: %s", exc)
            return
        items: list = []
        for action in data.get("onResponseReceivedActions", []):
            items += action.get("appendContinuationItemsAction", {}).get(
                "continuationItems", []
            )
        scope = items if items else data
        before = len(videos)
        _collect_lockups(scope, videos, seen, limit)
        endpoints = [
            renderer["continuationEndpoint"]
            for renderer in _walk_key(scope, "continuationItemRenderer")
            if "continuationEndpoint" in renderer
        ]
        if len(videos) >= limit or not endpoints or (items and len(videos) == before):
            return
        endpoint = endpoints[0]
        body = {
            "context": dict(
                context,
                clickTracking={"clickTrackingParams": endpoint.get("clickTrackingParams")},
            ),
            "continuation": endpoint["continuationCommand"]["token"],
        }


def _walk_key(node: object, key: str) -> list[dict]:
    """Every value stored under `key` anywhere in `node`."""
    found: list[dict] = []
    if isinstance(node, dict):
        value = node.get(key)
        if isinstance(value, dict):
            found.append(value)
        for child in node.values():
            found.extend(_walk_key(child, key))
    elif isinstance(node, list):
        for child in node:
            found.extend(_walk_key(child, key))
    return found


def _save_cookies(jar, cookie_file: Path, credentials: set[str]) -> None:
    """Write the rotated jar back, unless doing so would break the session.

    Seen in production: rewriting on every call eventually dropped LOGIN_INFO
    and SAPISID, leaving a file that still loaded fine but only ever fetched the
    anonymous home. Skipping the write keeps the pushed session usable.
    """
    missing = credentials - {c.name for c in jar}
    if missing:
        log.warning("keeping cookies as pushed: a rewrite would drop %s", ", ".join(sorted(missing)))
        return
    try:
        jar.save(str(cookie_file), ignore_discard=True, ignore_expires=True)
        cookie_file.chmod(0o600)
    except Exception as exc:  # a stale file is recoverable; a crash here is not
        log.warning("could not persist rotated cookies: %s", exc)
