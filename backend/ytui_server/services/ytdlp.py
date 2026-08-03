"""yt-dlp based search, listings, details and stream URL resolution.

All yt-dlp calls are blocking: they are wrapped with anyio.to_thread and a
global semaphore to bound server load.
"""

from __future__ import annotations

import asyncio
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


# TTL cache for full (non-flat) extract_info results. The web player hits
# /streams then /mpd for the same video back to back; without this, each
# request pays a full multi-second yt-dlp extraction.
_INFO_CACHE: dict[str, tuple[float, dict]] = {}
_INFO_CACHE_LOCK = threading.Lock()
_INFO_CACHE_TTL = 300.0  # seconds; stream URLs themselves last ~6 h
_INFO_CACHE_MAX = 32


def _extract_info_cached(url: str) -> dict:
    """extract_info with a small module-level TTL cache (thread-safe)."""
    now = time.monotonic()
    with _INFO_CACHE_LOCK:
        cached = _INFO_CACHE.get(url)
        if cached is not None and now - cached[0] < _INFO_CACHE_TTL:
            return cached[1]
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 15}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
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


async def playlist_videos(playlist_url: str, limit: int = 200) -> tuple[list[Video], str]:
    return await _run(_BULK_SEM, _extract_flat_info, playlist_url, limit)


def _extract_details(url: str) -> VideoDetails:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 15}
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

    # 2. Progressive (audio+video in one file)
    progressive = _capped(
        [
            f
            for f in formats
            if f.get("acodec") not in (None, "none")
            and f.get("vcodec") not in (None, "none")
        ],
        max_height,
    )
    if progressive:
        best = max(progressive, key=lambda f: f.get("height") or 0)
        return make("progressive", best["url"], height=best.get("height") or None)

    # 3. Split DASH: best video + best audio
    videos = _capped(
        [f for f in formats if f.get("vcodec") not in (None, "none")], max_height
    )
    audios = [
        f
        for f in formats
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    if videos and audios:
        bv = max(videos, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
        ba = max(audios, key=lambda f: f.get("abr") or f.get("tbr") or 0)
        return make(
            "split",
            bv["url"],
            video_url=bv["url"],
            audio_url=ba["url"],
            height=bv.get("height") or None,
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
    """Personalised YouTube home feed; needs the account cookies file."""
    return await _run(_BULK_SEM, _extract_recommended, cookie_file, limit)


def _extract_recommended(cookie_file: Path, limit: int) -> list[Video]:
    import yt_dlp

    opts = {
        **_YDL_FLAT_OPTS,
        # ":ytrec" resolves to a playlist of URLs: with extract_flat=True (the
        # module default) yt-dlp stops at the wrapper and yields zero entries.
        "extract_flat": "in_playlist",
        "playlist_items": f"1:{limit}",
        # `cookiefile` (not cookiesfrombrowser): YoutubeDL.close() saves the
        # jar back here, so Google's rotated cookies are persisted.
        "cookiefile": str(cookie_file),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(":ytrec", download=False)
    videos = (entry_to_item(e) for e in (info.get("entries") or []) if e)
    return [v for v in videos if v is not None]
