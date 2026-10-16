"""yt-dlp based search, listings, details and stream URL resolution.

All yt-dlp calls are blocking: they are wrapped with anyio.to_thread and a
global semaphore to bound server load.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

import anyio
import httpx

from ..models import StreamInfo, SubtitleTrackOut, Video, VideoDetails
from . import crowdbunker

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ytui-server/0.2"
# Only for the signed-in home feed: YouTube tailors that grid to the client.
_BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0"
# Cookies without which youtube.com serves the anonymous home (see _save_cookies).
_SESSION_COOKIES = ("LOGIN_INFO", "SAPISID", "__Secure-3PAPISID", "__Secure-1PAPISID")
# Home-feed pagination: each InnerTube page carries ~24 videos.
_HOME_MAX_PAGES = 12

# The pushed session (same file as the home feed): see set_cookie_file.
_cookie_file: Path | None = None
# Serializes rotated-cookie writes; the file itself is only ever replaced,
# never truncated in place, so readers need no lock.
_COOKIE_FILE_LOCK = threading.Lock()


def set_cookie_file(path: Path | None) -> None:
    global _cookie_file
    _cookie_file = path


log = logging.getLogger(__name__)
_YT_INITIAL_DATA_RE = re.compile(r"ytInitialData\s*=\s*(\{.*?\});</script>", re.DOTALL)


def _session_cookie_copy() -> Path | None:
    """A private temp copy of the pushed session jar, or None to stay anonymous.

    yt-dlp only reads cookies from `cookiefile` (an unknown `cookies` option is
    silently ignored) and rewrites that file IN PLACE when it closes. Handing it
    the live jar would let concurrent extractions read a half-written file, so
    each extraction gets its own copy; rotated cookies are merged back by
    _persist_rotated_cookies through the guarded, atomic _save_cookies.
    """
    if _cookie_file is None or not _cookie_file.exists():
        return None
    from yt_dlp.cookies import YoutubeDLCookieJar

    try:
        jar = YoutubeDLCookieJar(str(_cookie_file))
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        log.warning("session cookie file unreadable, staying anonymous: %s", exc)
        return None
    # LOGIN_INFO alone proves a signed-in session; a file that lost it only
    # ever fetches anonymous pages.
    if "LOGIN_INFO" not in {c.name for c in jar}:
        return None
    fd, name = tempfile.mkstemp(prefix="ytui-cookies-", suffix=".txt")  # 0600
    os.close(fd)
    copy = Path(name)
    try:
        jar.save(str(copy), ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        copy.unlink(missing_ok=True)
        log.warning("could not stage the session cookies, staying anonymous: %s", exc)
        return None
    return copy


def _persist_rotated_cookies(copy: Path) -> None:
    """Merge the jar yt-dlp rewrote into `copy` back into the live session file."""
    if _cookie_file is None:
        return
    from yt_dlp.cookies import YoutubeDLCookieJar

    try:
        jar = YoutubeDLCookieJar(str(copy))
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        log.warning("could not read the rotated session cookies: %s", exc)
        return
    _save_cookies(jar, _cookie_file)


def _youtube_dl(opts: dict):
    """A yt_dlp.YoutubeDL with ytui's upstream-extractor fixes applied."""
    import yt_dlp

    crowdbunker.patch_ytdlp_extractors()
    return yt_dlp.YoutubeDL(opts)


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
# What a live's bucket can only be judged on: its behaviour a minute later. The
# experiment ids in `fexp` are the one thing that identifies the bucket an
# extraction landed in, and YouTube renumbers them at will — so nothing is
# hardcoded. Instead, a live that is served gets re-probed once its bucket has
# shown its hand; a signature caught refusing its own segments is quarantined,
# and later extractions carrying it are thrown away and rolled again.
_FEXP_RE = re.compile(r"(?:/fexp/|[?&]fexp=)([\d,]+)")
_BUCKET_VERIFY_DELAY = 70.0  # measured: served at +26 s, refused from +51 s on
_BUCKET_QUARANTINE_TTL = 3600.0
_BUCKET_QUARANTINE_MAX = 32
_capped_buckets: dict[str, float] = {}  # fexp signature -> monotonic expiry
_bucket_checks_pending: set[str] = set()
_BUCKET_LOCK = threading.Lock()


def _fexp_signature(url: str) -> str | None:
    """The experiment ids that identify the A/B bucket an extraction landed in."""
    found = _FEXP_RE.search(url)
    return found.group(1) if found else None


def _live_bucket_signature(info: dict) -> str | None:
    target = _hls_probe_target(info.get("formats") or [])
    return _fexp_signature(target) if target else None


def _bucket_quarantined(signature: str | None) -> bool:
    if signature is None:
        return False
    with _BUCKET_LOCK:
        expiry = _capped_buckets.get(signature)
        if expiry is None:
            return False
        if time.monotonic() >= expiry:
            del _capped_buckets[signature]
            return False
        return True


def _quarantine_bucket(signature: str) -> None:
    with _BUCKET_LOCK:
        now = time.monotonic()
        for key, expiry in list(_capped_buckets.items()):
            if now >= expiry:
                del _capped_buckets[key]
        if (
            signature not in _capped_buckets
            and len(_capped_buckets) >= _BUCKET_QUARANTINE_MAX
        ):
            oldest = min(_capped_buckets, key=lambda key: _capped_buckets[key])
            del _capped_buckets[oldest]
        _capped_buckets[signature] = now + _BUCKET_QUARANTINE_TTL


def _spawn_delayed(delay: float, func, *args) -> None:
    """Run func(*args) later on a daemon thread (seam for the tests)."""
    timer = threading.Timer(delay, func, args)
    timer.daemon = True
    timer.start()


def _verify_bucket(signature: str, playlist_url: str) -> None:
    """Quarantine a signature whose live segments have stopped being served."""
    try:
        with httpx.Client(timeout=_TRUNCATION_PROBE_TIMEOUT) as client:
            dead = _hls_segment_forbidden(client, playlist_url)
    except httpx.HTTPError as exc:  # a hiccup is not evidence of a bad bucket
        log.debug("bucket check failed for %s: %s", signature, exc)
        dead = False
    finally:
        with _BUCKET_LOCK:
            _bucket_checks_pending.discard(signature)
    if dead:
        log.warning(
            "youtube bucket %s stopped serving live segments, quarantining for %d min",
            signature,
            int(_BUCKET_QUARANTINE_TTL // 60),
        )
        _quarantine_bucket(signature)


def _schedule_bucket_check(info: dict) -> None:
    """Arrange for a served live to be re-probed once its bucket would have shown."""
    if not info.get("is_live"):
        return
    playlist = _hls_probe_target(info.get("formats") or [])
    signature = _fexp_signature(playlist) if playlist else None
    if playlist is None or signature is None:
        return
    with _BUCKET_LOCK:
        if signature in _bucket_checks_pending or signature in _capped_buckets:
            return
        _bucket_checks_pending.add(signature)
    _spawn_delayed(_BUCKET_VERIFY_DELAY, _verify_bucket, signature, playlist)


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
    """Cheapest googlevideo *video* HLS media playlist to sample a segment from.

    Picking the lowest-bitrate variant keeps the probe cheap. It must carry
    video: the bucket poisons every video variant of an extraction alike, but
    the audio-only rendition keeps being served (measured 2026-08-19: audio
    itag 233 answered 200 at +70 s while every video itag was refused from
    +26 s on) — probing it declared capped extractions healthy for weeks.
    """
    best: tuple[float, str] | None = None
    audio_best: tuple[float, str] | None = None
    for fmt in formats:
        url = fmt.get("url") or ""
        if not _HLS_PLAYLIST_RE.match(url):
            continue
        weight = float(fmt.get("tbr") or fmt.get("height") or 0)
        if fmt.get("vcodec") in (None, "none"):
            if audio_best is None or weight < audio_best[0]:
                audio_best = (weight, url)
            continue
        if best is None or weight < best[0]:
            best = (weight, url)
    chosen = best or audio_best
    return None if chosen is None else chosen[1]


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


def _adaptive_head_dead(info: dict) -> bool:
    """True when the split pair a player would pick refuses its first byte.

    Measured 2026-10-16 from serv: once Deno solves the n-challenge, anonymous
    extractions serve the TV client's adaptive formats (c=TVHTML5), whose URLs
    can 403 from byte 0 for a given video — every re-extraction alike — while
    the EOF truncation probe may look at a healthy audio of another client.
    Probes the best avc1 video-only and best audio-only googlevideo formats
    (what /mpd and the split stream would serve) with a 2-byte range.
    """
    formats = [
        f
        for f in info.get("formats") or []
        if _GOOGLEVIDEO_RE.match(f.get("url") or "")
        and not (f.get("protocol") or "").startswith("m3u8")
    ]
    videos = [
        f for f in formats
        if f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none")
    ]
    videos = [f for f in videos if (f.get("vcodec") or "").startswith("avc1")] or videos
    audios = [
        f for f in formats
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    targets: list[str] = []
    if videos:
        targets.append(max(videos, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))["url"])
    if audios:
        targets.append(
            max(audios, key=lambda f: (_lang_rank(f), f.get("abr") or f.get("tbr") or 0))["url"]
        )
    if not targets:
        return False
    try:
        with httpx.Client(timeout=_TRUNCATION_PROBE_TIMEOUT) as client:
            for target in targets:
                if client.get(target, headers={"Range": "bytes=0-1"}).status_code == 403:
                    return True
    except httpx.HTTPError as exc:  # network hiccup: assume the stream is fine
        log.debug("adaptive head probe failed: %s", exc)
    return False


def _drop_dead_muxed_formats(info: dict) -> None:
    """Remove muxed googlevideo formats whose URL refuses its very first byte.

    PO-token enforcement reached android_vr (2026-08): its muxed 360p (itag 18)
    answers 403 from byte 0 while the visionos adaptive formats stay healthy.
    Selection prefers a muxed file on a height tie, so one dead muxed format
    would silently break every ≤360p request. Re-extracting cannot help — the
    client is dead, not the bucket — so the format is dropped instead, at the
    cost of one cheap ranged GET per (cached) extraction.
    """
    formats = info.get("formats") or []
    muxed = [
        f
        for f in formats
        if _GOOGLEVIDEO_RE.match(f.get("url") or "")
        and f.get("vcodec") not in (None, "none")
        and f.get("acodec") not in (None, "none")
    ]
    if not muxed:
        return
    dead: set[str] = set()
    try:
        with httpx.Client(timeout=_TRUNCATION_PROBE_TIMEOUT) as client:
            for fmt in muxed:
                resp = client.get(fmt["url"], headers={"Range": "bytes=0-1"})
                if resp.status_code == 403:
                    dead.add(fmt["url"])
    except httpx.HTTPError as exc:  # network hiccup: keep the formats
        log.debug("muxed format probe failed: %s", exc)
        return
    if dead:
        log.warning(
            "dropping %d muxed format(s) refusing their first byte (403)", len(dead)
        )
        info["formats"] = [f for f in formats if f.get("url") not in dead]


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


def _media_stream_id(url: str) -> str | None:
    """The per-extraction `id` of a googlevideo media URL (query or path form).

    VOD `videoplayback` URLs carry `?id=o-…` shared by every format of one
    extraction; live playlists and segments carry the same value path-style
    (`/id/<video>.<n>/`). Non-googlevideo URLs have no such identity: None.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not (host == "googlevideo.com" or host.endswith(".googlevideo.com")):
        return None
    qs_id = (parse_qs(parsed.query).get("id") or [None])[0]
    if qs_id:
        return qs_id
    found = re.search(r"/id/([^/]+)", parsed.path)
    return found.group(1) if found else None


def evict_cached_info(url: str) -> None:
    """Drop the cached extraction for a watch URL so the next call is fresh.

    The live-HLS proxy re-extracts on its own schedule (segment URLs die
    ~26 s after extraction in the capped bucket); the shared info cache must
    not hand it back the extraction it is trying to replace.
    """
    with _INFO_CACHE_LOCK:
        _INFO_CACHE.pop(url, None)


def forget_dead_stream(media_url: str) -> bool:
    """Evict cached extractions that handed out this now-dead googlevideo URL.

    The proxy is the one place that observes a stream URL actually dying
    (upstream 403 mid-playback: PO-token buckets, expiry, IP churn). Without
    this, a client that re-requests /streams or /mpd precisely because its
    stream died gets the very same dead URLs back from the info cache for up
    to five minutes; with it, the single client-side retry re-extracts.
    """
    stream_id = _media_stream_id(media_url)
    if stream_id is None:
        return False
    evicted = False
    with _INFO_CACHE_LOCK:
        for key, (_, info) in list(_INFO_CACHE.items()):
            urls = [info.get("url"), info.get("manifest_url")]
            urls.extend(f.get("url") for f in info.get("formats") or [])
            if any(u and _media_stream_id(u) == stream_id for u in urls):
                del _INFO_CACHE[key]
                evicted = True
    if evicted:
        log.warning(
            "stream %s died mid-play (upstream 403), evicted cached extraction",
            stream_id,
        )
    return evicted


class _Inflight:
    """One extraction in progress, awaited by every concurrent caller."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.info: dict | None = None
        self.error: BaseException | None = None


# The web player asks for /streams and /mpd of the same video at once: without
# this, both miss the (not yet filled) cache and pay a full extraction each.
_INFLIGHT: dict[str, _Inflight] = {}


def _extract_info_cached(url: str) -> dict:
    """extract_info with a small module-level TTL cache (thread-safe).

    Concurrent callers for the same URL share one extraction: the first runs
    it, the others wait for its result (or its exception).
    """
    now = time.monotonic()
    with _INFO_CACHE_LOCK:
        cached = _INFO_CACHE.get(url)
        if cached is not None and now - cached[0] < _info_cache_ttl(cached[1]):
            return cached[1]
        pending = _INFLIGHT.get(url)
        owner = pending is None
        if owner:
            pending = _INFLIGHT[url] = _Inflight()
    assert pending is not None
    if not owner:
        pending.done.wait()
        if pending.error is not None:
            raise pending.error
        assert pending.info is not None
        return pending.info
    try:
        pending.info = _extract_info_fresh(url)
        return pending.info
    except BaseException as exc:
        pending.error = exc
        raise
    finally:
        with _INFO_CACHE_LOCK:
            if _INFLIGHT.get(url) is pending:
                del _INFLIGHT[url]
        pending.done.set()


class _DeadAdaptive(Exception):
    """An extraction whose split video/audio pair refuses its very first byte."""

    def __init__(self, info: dict) -> None:
        super().__init__("adaptive formats refuse their first byte")
        self.info = info


# Set on a cached info when every extraction path served dead adaptive URLs:
# the split/DASH path is skipped and the HLS master (with its audio
# renditions) is served instead.
_ADAPTIVE_DEAD_KEY = "_ytui_adaptive_dead"


def _extract_info_fresh(url: str) -> dict:
    """One full extraction (with the truncation guard), stored in the cache.

    YouTube goes through the pushed session first when there is one (measured
    2026-10-16: the anonymous TV client's adaptive URLs can 403 from byte 0 on
    a datacenter IP, the session's web-embedded ones do not), then anonymously
    if the session extraction fails or serves dead URLs. A dead or flagged
    session must never break playback.
    """
    bitchute = "bitchute.com" in url
    cookie_copy = None if bitchute else _session_cookie_copy()
    attempts: list[Path | None] = [cookie_copy, None] if cookie_copy is not None else [None]
    dead_info: dict | None = None
    try:
        for i, cookies in enumerate(attempts):
            last = i == len(attempts) - 1
            try:
                info = _extract_info_once(url, bitchute, cookies)
            except _DeadAdaptive as dead:
                dead_info = dead.info
                if not last:
                    log.warning(
                        "session extraction of %s served adaptive URLs refusing "
                        "their first byte; retrying anonymously",
                        url,
                    )
                continue
            except Exception as exc:
                if not last:
                    log.warning(
                        "stream extraction with session cookies failed for %s (%s); "
                        "retrying anonymously",
                        url,
                        exc,
                    )
                    continue
                if dead_info is None:
                    raise
                break
            return _store_info(url, bitchute, info)
    finally:
        if cookie_copy is not None:
            cookie_copy.unlink(missing_ok=True)
    # Every path served dead adaptive URLs: keep the formats that may still
    # play (HLS master, muxed) and steer _extract_streams away from DASH.
    assert dead_info is not None
    log.warning("every extraction of %s served dead adaptive URLs; HLS only", url)
    dead_info[_ADAPTIVE_DEAD_KEY] = True
    return _store_info(url, bitchute, dead_info)


def _extract_info_once(url: str, bitchute: bool, cookies: Path | None) -> dict:
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 15}
    if bitchute:
        opts["check_formats"] = False  # see _bitchute_media_url
    if cookies is not None:
        opts["cookiefile"] = str(cookies)
    info = {}
    for attempt in range(1, _TRUNCATION_ATTEMPTS + 1):
        with _youtube_dl(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if cookies is not None:
            # Google rotates session cookies on every request; persisting them
            # is what keeps the pushed session alive (same as the home feed).
            _persist_rotated_cookies(cookies)
        if attempt == _TRUNCATION_ATTEMPTS:
            break  # a capped stream still beats no stream at all
        quarantined = _bucket_quarantined(_live_bucket_signature(info))
        healthy = not quarantined and not _delivery_truncated(info)
        # A client refused from byte 0 is dead, not an unlucky bucket:
        # re-rolling the same path cannot help (measured 3/3), switch paths.
        if _adaptive_head_dead(info):
            raise _DeadAdaptive(info)
        if healthy:
            break
        log.warning(
            "youtube served %s stream URLs for %s, re-resolving (%d/%d)",
            "quarantined-bucket" if quarantined else "~60 s-capped",
            url,
            attempt,
            _TRUNCATION_ATTEMPTS,
        )
    return info


def _store_info(url: str, bitchute: bool, info: dict) -> dict:
    if bitchute:
        _fix_bitchute_formats(info)
    _drop_dead_muxed_formats(info)
    # Served now, judged later: a live's bucket only reveals itself a minute in.
    _schedule_bucket_check(info)
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
        view_count=entry.get("view_count"),
        kind=kind,
        platform=platform,
    )


def _flat_listing(
    url: str, limit: int | None = None, offset: int = 0
) -> tuple[list[Video], dict]:
    """Flat entries of a container (search, channel tab, playlist) and its info."""
    opts = dict(_YDL_FLAT_OPTS)
    if limit:
        opts["playlist_items"] = f"{offset + 1}:{offset + limit}"
    elif offset:
        opts["playlist_items"] = f"{offset + 1}:"
    with _youtube_dl(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    items: list[Video] = []
    for entry in info.get("entries") or []:
        item = entry_to_item(entry)
        if item:
            items.append(item)
    return items, info


def _extract_flat(url: str, limit: int | None = None, offset: int = 0) -> list[Video]:
    return _flat_listing(url, limit, offset)[0]


def _extract_flat_info(url: str, limit: int | None = None) -> tuple[list[Video], str]:
    """Like _extract_flat but also returns the container title (playlist name)."""
    items, info = _flat_listing(url, limit)
    return items, info.get("title") or ""


def _extract_channel_playlists(url: str, limit: int, offset: int) -> list[Video]:
    try:
        items, info = _flat_listing(url, limit, offset)
    except Exception as exc:
        # A channel without playlists has no Playlists tab at all.
        if "does not have a playlists tab" in str(exc):
            return []
        raise
    # Playlist lockups carry no owner: yt-dlp reports "Playlist" as their
    # channel. The tab's own info names the channel.
    channel_title = info.get("channel") or info.get("uploader") or ""
    channel_id = info.get("channel_id") or ""
    for item in items:
        item.channel_title = channel_title
        item.channel_id = channel_id or item.channel_id
    return items


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


# Without the UC id there is no popular playlist: this many newest uploads
# are ranked locally instead.
_POPULAR_SCAN_CAP = 200


def _extract_popular(url: str, limit: int, offset: int) -> list[Video]:
    try:
        return _extract_flat(url, limit, offset)
    except Exception as exc:
        # Channels without long-form uploads never get the playlist.
        if "does not exist" in str(exc):
            return []
        raise


async def channel_popular_videos(
    channel_id: str, limit: int = 50, offset: int = 0
) -> list[Video]:
    """Most viewed videos of a YouTube channel, most viewed first.

    The Videos tab's "Popular" chip has no URL form (?sort=p is ignored
    nowadays), but YouTube keeps an auto-generated "Popular videos"
    playlist per channel: UULP + the channel id without its UC prefix
    (long-form videos, as the Videos tab; capped at 200 entries upstream).
    """
    if not channel_id.startswith("UC"):
        url = Video(video_id=channel_id, title="", kind="channel").url
        items = await channel_videos(url, limit=_POPULAR_SCAN_CAP)
        items.sort(key=lambda v: v.view_count or 0, reverse=True)
        return items[offset : offset + limit]
    url = f"https://www.youtube.com/playlist?list=UULP{channel_id[2:]}"
    return await _run(_BULK_SEM, _extract_popular, url, limit, offset)


async def channel_playlists(
    channel_url: str, limit: int = 50, offset: int = 0
) -> list[Video]:
    """Playlists of a YouTube channel (its Playlists tab), in the tab's order.

    Only YouTube serves this tab; callers must not route other platforms here.
    """
    url = f"{channel_url.rstrip('/')}/playlists"
    return await _run(_BULK_SEM, _extract_channel_playlists, url, limit, offset)


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


def _is_youtube_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be"


def _extract_details(url: str) -> VideoDetails:
    if _is_youtube_url(url):
        # Same extraction as /streams (TTL cache + in-flight sharing): the
        # watch page asks for both at once, and a details page followed by
        # "Lire" no longer pays two full yt-dlp runs.
        return _details_from_info(_extract_info_cached(url), url)

    # check_formats: /details never serves a stream URL, so BitChute's seed
    # probing would only cost a pointless HEAD round.
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 15,
        "check_formats": False,
    }
    with _youtube_dl(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return _details_from_info(info, url)


def _details_from_info(info: dict, url: str) -> VideoDetails:
    upload_date = info.get("upload_date") or ""
    if len(upload_date) == 8:
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    duration = info.get("duration")
    video_id = info.get("id") or ""
    thumbnail = info.get("thumbnail") or ""
    if "youtube.com" in url and video_id:
        # Same small derived thumbnail as every YouTube listing (yt-dlp's
        # pick is the multi-hundred-KB maxres one).
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
    return VideoDetails(
        video_id=video_id,
        title=info.get("title") or "",
        channel_id=info.get("channel_id") or "",
        channel_title=info.get("channel") or info.get("uploader") or "",
        description=info.get("description") or "",
        view_count=info.get("view_count"),
        like_count=info.get("like_count"),
        duration=int(duration) if duration else None,
        upload_date=upload_date,
        thumbnail_url=thumbnail,
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
    raw_views = info.get("view_count")
    view_count = raw_views if isinstance(raw_views, int) else None
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
            view_count=view_count,
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
            # Original soundtrack first, then bitrate. No Opus preference
            # here: this path hands a raw audio URL to the client, so the
            # codec choice must stay what it always was (top-bitrate m4a) —
            # a <audio src> consumer (Safari/iOS) cannot play WebM/Opus.
            best = max(
                audio,
                key=lambda f: (_lang_rank(f), f.get("abr") or f.get("tbr") or 0),
            )
            return make("progressive", best["url"])
        # fall through: use whatever combined format exists

    # 0. Every extraction path served split URLs refusing their first byte
    # (see _extract_info_fresh): the HLS master still plays. Its variants are
    # video-only (audio comes from its EXT-X-MEDIA renditions), so only the
    # master qualifies, and the cap is advisory on it.
    if info.get(_ADAPTIVE_DEAD_KEY) and not audio_only:
        hls_video = [
            f
            for f in formats
            if (f.get("protocol") or "").startswith("m3u8")
            and f.get("vcodec") not in (None, "none")
            and f.get("manifest_url")
        ]
        if hls_video:
            best = max(_capped(hls_video, max_height), key=lambda f: f.get("height") or 0)
            return make("hls", best["manifest_url"], height=best.get("height") or None)

    # 1. HLS manifest (adaptive; best for mobile players).
    # acodec == "none" is a video-only variant: its audio lives in separate
    # renditions only reachable through a master manifest — which YouTube VODs
    # (visionos client) do not expose. Serving such a playlist alone plays a
    # silent video, so those never qualify; the split path covers them.
    # acodec None (unknown, e.g. Twitch masters) stays eligible.
    hls_all = [
        f
        for f in formats
        if f.get("protocol", "").startswith("m3u8")
        and f.get("vcodec") not in (None, "none")
        and f.get("acodec") != "none"
    ]
    # A signed-in extraction (2026-10) lists muxed HLS variants once per audio
    # language (original + auto-dubs) all in one master: keep the original
    # soundtrack's variants, and serve a variant (never that master, where the
    # player would pick by bandwidth and could land on a dub).
    multilingual = len({_lang_rank(f) for f in hls_all}) > 1
    if multilingual:
        top_rank = max(_lang_rank(f) for f in hls_all)
        hls_all = [f for f in hls_all if _lang_rank(f) == top_rank]
    if hls_all:
        hls = _capped(hls_all, max_height)
        best = max(hls, key=lambda f: f.get("height") or 0)
        height = best.get("height") or None
        top = max((f.get("height") or 0) for f in hls_all)
        # The master manifest lets the player adapt, but it carries every
        # variant: only serve it when the cap took nothing away.
        if multilingual or top > (best.get("height") or 0):
            return make("hls", best["url"], height=height)
        manifest = info.get("manifest_url") or ""
        return make("hls", manifest or best["url"], height=height)

    # 1b. Live with split HLS tracks (yt-dlp >= 2026.08 reports YouTube lives
    # as video-only variants + separate audio renditions): a bare variant
    # plays silent, but every format carries the master manifest URL, whose
    # EXT-X-MEDIA audio renditions the player resolves itself. The cap is
    # advisory here (the master lists every variant); the live-HLS proxy
    # (services/livehls.py) enforces it for clients that play through it.
    if info.get("is_live"):
        live_video = [
            f
            for f in formats
            if f.get("protocol", "").startswith("m3u8")
            and f.get("vcodec") not in (None, "none")
            and f.get("manifest_url")
        ]
        if live_video:
            best = max(_capped(live_video, max_height), key=lambda f: f.get("height") or 0)
            return make("hls", best["manifest_url"], height=best.get("height") or None)

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
    # À hauteur égale, un fichier direct (https) bat une playlist m3u8 vidéo
    # seule (visionos en expose aux tbr plus élevés) : seek par Range, expiry
    # parsable, et même chemin de lecture qu'historiquement pour le split.
    bv = (
        max(
            videos,
            key=lambda f: (
                f.get("height") or 0,
                not (f.get("protocol") or "").startswith("m3u8"),
                f.get("tbr") or 0,
            ),
        )
        if videos
        else None
    )

    # -1 keeps an absent candidate below a real 0-height one (unknown height).
    prog_height = (best_prog.get("height") or 0) if best_prog else -1
    split_height = (bv.get("height") or 0) if bv else -1

    if bv is not None and split_height > prog_height:
        ba = max(audios, key=_audio_rank)
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


def _lang_rank(f: dict) -> int:
    """Rank a format's audio language: original > unknown > dubbed.

    YouTube lists auto-dubbed renditions next to the original soundtrack at
    comparable bitrates: a plain bitrate max() used to hand a French video
    its English auto-dub. The reliable signal is language_preference: yt-dlp
    sets it to 10 on the default (original) track and -1 on dubs —
    format_note is not enough, dubs often read just "German (DE), low" with
    no "dub" marker at all. Formats without the field (Odysee, Twitch…)
    rank 0, above dubs and below an explicit original; a "dub" note stays
    as a fallback penalty for extractors that only mark it there.
    """
    pref = f.get("language_preference")
    if pref:
        return pref
    return -1 if "dub" in (f.get("format_note") or "").lower() else 0


def _audio_rank(f: dict) -> tuple:
    """Order audio-only formats for the split path, best last.

    Original soundtrack first (see _lang_rank). Opus/WebM then beats
    AAC/m4a — same bitrates, and the m4a rendition is what the mobile
    player's demuxer failed to resume-seek through the proxy (stuck
    `seeking=yes`, empty audio-pts) while Opus seeked fine.
    """
    return (
        _lang_rank(f),
        (f.get("acodec") or "").startswith("opus"),
        f.get("abr") or f.get("tbr") or 0,
    )


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
    if info.get(_ADAPTIVE_DEAD_KEY):
        return None  # dead split pair: /streams serves the HLS master instead
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
    # Original first (see _lang_rank); constrained to mp4a here so no
    # Opus preference applies.
    ba = max(
        audios,
        key=lambda f: (_lang_rank(f), f.get("abr") or f.get("tbr") or 0),
    )
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

_COMPACT_COUNT_RE = re.compile(r"^(\d+(?:\.\d{1,2})?)([KMB])?$", re.IGNORECASE)
_COMPACT_MULTIPLIERS = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}

def _parse_compact_count(text: str) -> int | None:
    """'144K' / '1.2M' / '1,815,671' -> int; anything else (dates, labels) -> None."""
    match = _COMPACT_COUNT_RE.match(re.sub(r"[,\s]", "", text or ""))
    if not match:
        return None
    return int(float(match.group(1)) * _COMPACT_MULTIPLIERS[(match.group(2) or "").upper()])


# Channel /videos lockups carry only relative time ("il y a 21 h", "5 days ago")
# and a compact view count in the account's language (often French "1,6 k").
_REL_TIME_FR_RE = re.compile(r"il y a\s+(\d+)\s*([a-zé]+)", re.IGNORECASE)
_REL_TIME_EN_RE = re.compile(r"(\d+)\s+([a-z]+)\s+ago", re.IGNORECASE)
_REL_TIME_UNITS = {
    "min": 60, "mins": 60, "h": 3_600, "j": 86_400, "sem": 604_800,
    "mois": 2_592_000, "an": 31_536_000, "ans": 31_536_000,
    "minute": 60, "minutes": 60, "hour": 3_600, "hours": 3_600,
    "day": 86_400, "days": 86_400, "week": 604_800, "weeks": 604_800,
    "month": 2_592_000, "months": 2_592_000, "year": 31_536_000, "years": 31_536_000,
}
_FR_COUNT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*([KMB])?", re.IGNORECASE)


def _parse_relative_time(text: str) -> datetime | None:
    """'il y a 21 h' / '5 days ago' -> an approximate datetime, else None.

    The channel /videos grid shows relative time only, so this is as precise as
    YouTube provides — enough for feed sorting and the <3-days new-video filter.
    """
    lowered = (text or "").strip().lower()
    match = _REL_TIME_FR_RE.search(lowered) or _REL_TIME_EN_RE.search(lowered)
    if not match:
        return None
    seconds = _REL_TIME_UNITS.get(match.group(2))
    if seconds is None:
        return None
    return datetime.now(timezone.utc) - timedelta(seconds=int(match.group(1)) * seconds)


def _parse_compact_count_fr(text: str) -> int | None:
    """Compact view count, French or English: '1,6 k' -> 1600, '2.3M' -> 2_300_000."""
    cleaned = (text or "").replace("\xa0", " ").strip()
    match = _FR_COUNT_RE.search(cleaned)
    if not match:
        return None
    number = match.group(1).replace(",", ".")
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(
        (match.group(2) or "").lower(), 1
    )
    try:
        return int(float(number) * multiplier)
    except ValueError:
        return None


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
    # Watch-next lockups (InnerTube /next) carry a second row: ["144K", "4d ago"].
    view_count: int | None = None
    for row in rows[1:]:
        for part in row.get("metadataParts") or []:
            view_count = _parse_compact_count(((part.get("text") or {}).get("content")) or "")
            if view_count is not None:
                break
        if view_count is not None:
            break

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
        view_count=view_count,
        kind="video",
        platform="youtube",
    )


# The anonymous WEB context for InnerTube /next. The lockups it returns are
# the browser's "Up next" list — unlike the watch page's initial ytInitialData
# (channel name only), every lockup carries a ["144K", "4d ago"] metadata row,
# which is the only cheap source of view counts for suggestions.
_INNERTUBE_WEB_CONTEXT = {
    "client": {
        "clientName": "WEB",
        "clientVersion": "2.20250101.00.00",
        "hl": "en",
        "gl": "US",
    }
}

async def related_videos(video_id: str, limit: int = 20) -> list[Video]:
    """YouTube's "Up next" for a video, with view counts where available."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US"}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as http_client:
            data: dict = {}
            try:
                resp = await http_client.post(
                    "https://www.youtube.com/youtubei/v1/next?prettyPrint=false",
                    json={"context": _INNERTUBE_WEB_CONTEXT, "videoId": video_id},
                    headers={
                        **headers,
                        "Content-Type": "application/json",
                        "Origin": "https://www.youtube.com",
                        "Referer": url,
                    },
                )
                if resp.status_code == 429:
                    raise UpstreamError("YouTube rate-limited related videos (429)")
                resp.raise_for_status()
                data = resp.json() or {}
            except UpstreamError:
                raise
            except Exception as exc:
                log.debug("related /next failed, falling back to the watch page: %s", exc)
            if not data.get("contents"):
                # Watch-page HTML: same list shape, but no view counts.
                resp = await http_client.get(url, headers=headers)
                if resp.status_code == 429:
                    raise UpstreamError("YouTube rate-limited related videos (429)")
                resp.raise_for_status()
                match = _YT_INITIAL_DATA_RE.search(resp.text)
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


def _channel_title_from_html(html: str) -> str:
    """The channel's display name from its /videos page (og:title, else <title>)."""
    match = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
    if match:
        return match.group(1).strip()
    match = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
    if match:
        title = match.group(1).strip()
        for suffix in (" - YouTube", " - YouTube FR", " - YouTube UK"):
            if title.endswith(suffix):
                return title[: -len(suffix)]
    return ""


def _channel_video_meta(lockup: dict) -> tuple[datetime | None, int | None]:
    """(published, view_count) from a channel /videos lockup's metadata row.

    The row is a flat [count, time-ago] pair (the channel name is omitted on the
    channel's own page), in any order and either language.
    """
    metadata = (lockup.get("metadata") or {}).get("lockupMetadataViewModel") or {}
    rows = (
        (metadata.get("metadata") or {}).get("contentMetadataViewModel") or {}
    ).get("metadataRows") or []
    published: datetime | None = None
    view_count: int | None = None
    for row in rows:
        for part in row.get("metadataParts") or []:
            text = (part.get("text") or {}).get("content") or ""
            if published is None and (candidate := _parse_relative_time(text)) is not None:
                published = candidate
                continue
            if view_count is None:
                view_count = _parse_compact_count_fr(text)
    return published, view_count


async def channel_videos_page(
    cookie_file: Path, channel_id: str, limit: int = 15, fallback_title: str = ""
) -> list[Video]:
    """A channel's /videos tab, scraped with the pushed session.

    The public RSS endpoint (feeds/videos.xml?channel_id=) has returned 404
    intermittently/globally since ~Dec 2025 with no Google acknowledgment, so the
    feed reads the same channel page the browser shows. This yields the channel
    name and relative publish time, which RSS never carried. Returns [] when the
    session no longer authenticates (callers fall back to RSS, then stale cache).
    """
    from yt_dlp.cookies import YoutubeDLCookieJar

    jar = YoutubeDLCookieJar(str(cookie_file))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        raise UpstreamError(f"unreadable cookie file: {exc}") from exc
    if not ({c.name for c in jar} & set(_SESSION_COOKIES)):
        return []  # an anonymous /videos page can hit the EU consent wall (serv IP)

    async with httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=True,
        cookies=jar,
        # Browser UA, no Accept-Language: the account's language wins, and the
        # server UA gets a thinner page (18 vs 23 items, measured on the home grid).
        headers={"User-Agent": _BROWSER_UA},
    ) as http_client:
        resp = await http_client.get(f"https://www.youtube.com/channel/{channel_id}/videos")
        if resp.status_code == 429:
            raise UpstreamError("YouTube rate-limited the channel feed (429)")
        resp.raise_for_status()
        html = resp.text
        match = _YT_INITIAL_DATA_RE.search(html)
        if not match:
            raise UpstreamError("ytInitialData not found")
        data = json.loads(match.group(1))
    title = _channel_title_from_html(html) or fallback_title

    videos: list[Video] = []
    seen: set[str] = set()
    for lockup in _walk_lockups(data):
        if len(videos) >= limit:
            break
        video = _lockup_to_video(lockup)
        if video is None or video.video_id in seen:
            continue
        seen.add(video.video_id)
        # _lockup_to_video mis-reads the channel page's single [count, time] row
        # as the channel name; the name is the page's, and the row holds the time.
        video.channel_title = title
        published, view_count = _channel_video_meta(lockup)
        if video.published is None:
            video.published = published
        if video.view_count is None:
            video.view_count = view_count
        videos.append(video)
    if not videos:
        raise UpstreamError("no video lockups in channel page")
    return videos


async def home_recommendations(
    cookie_file: Path, limit: int = 50, page_id: str = ""
) -> list[Video]:
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

    `page_id` acts as a brand channel of the account (see
    music.page_id_for): the HTML page always shows the identity the session
    is on, so the grid then comes from InnerTube only, where the page id is
    honoured per request.
    """
    from yt_dlp.cookies import YoutubeDLCookieJar

    jar = YoutubeDLCookieJar(str(cookie_file))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        raise UpstreamError(f"unreadable cookie file: {exc}") from exc

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
            if not page_id:
                _collect_lockups(data, videos, seen, limit)
            if len(videos) < limit:
                await _home_continuations(
                    http_client, jar, html, videos, seen, limit, page_id=page_id
                )
    except UpstreamError:
        raise
    except Exception as exc:
        log.warning("home recommendations failed: %s", exc)
        raise UpstreamError(str(exc)) from exc

    # Google rotates session cookies on every request; persisting them is what
    # keeps the pushed session from lapsing.
    _save_cookies(jar, cookie_file)
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
    page_id: str = "",
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
    if page_id:
        context = {**context, "user": {**context.get("user", {}), "onBehalfOfUser": page_id}}
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
    if page_id:
        headers["X-Goog-PageId"] = page_id
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


def _save_cookies(jar, cookie_file: Path) -> None:
    """Write the rotated jar back atomically, unless doing so would break the session.

    Seen in production: rewriting on every call eventually dropped LOGIN_INFO
    and SAPISID, leaving a file that still loaded fine but only ever fetched the
    anonymous home. A rewrite must therefore keep LOGIN_INFO and at least one
    SAPISID-family cookie; anything else is skipped and the file stays as pushed.
    The write is a tmp file + os.replace (never a truncated live file) so a
    concurrent reader can never see a partial jar.
    """
    names = {c.name for c in jar}
    if "LOGIN_INFO" not in names or not (names & {"SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"}):
        log.warning(
            "keeping cookies as pushed: the rotated jar would drop the session credentials"
        )
        return
    with _COOKIE_FILE_LOCK:
        try:
            tmp = cookie_file.with_name(cookie_file.name + f".tmp{os.getpid()}")
            jar.save(str(tmp), ignore_discard=True, ignore_expires=True)
            os.replace(tmp, cookie_file)
            cookie_file.chmod(0o600)
        except Exception as exc:  # a stale file is recoverable; a crash here is not
            log.warning("could not persist rotated cookies: %s", exc)
