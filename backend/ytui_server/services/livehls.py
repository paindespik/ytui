"""Self-healing HLS proxy sessions for YouTube lives.

YouTube currently enrols every anonymous session of this server's IP in a
bucket whose live *segment* URLs stop being served ~26 s after extraction
(measured 2026-08-19: 200 at +14 s, 403 from +26 s on, every video variant at
once, while the playlist URL keeps answering 200 indefinitely). No
client-side buffering can ride that out, so clients gag on a frozen picture
every ~75 s.

The cure lives here: the client plays *stable* playlists served by this
process. The entry playlist is the upstream master rewritten so every variant
and audio rendition points at ``media/{itag}.m3u8`` (itags are stable across
extractions); each media playlist maps segment sequence numbers (the
``/sq/N/`` path element, also stable) to their current upstream URLs and
rewrites the segments to ``{itag}/{sq}``. When an upstream segment answers
403 — or the extraction ages past the measured lifetime — the session
re-extracts and remaps the same itags and sequence numbers onto the fresh
URLs. The player never sees the swap: its buffer keeps draining while the
session heals underneath.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

from . import ytdlp

log = logging.getLogger(__name__)

_SQ_RE = re.compile(r"/sq/(\d+)/")
_ITAG_RE = re.compile(r"/itag/(\d+)/")
_RESOLUTION_RE = re.compile(r"RESOLUTION=\d+x(\d+)")
_URI_ATTR_RE = re.compile(r'URI="([^"]+)"')

# Segment URLs die ~26 s after extraction; start refreshing well before that
# so a fresh map is ready without the player ever hitting a 403.
REFRESH_AFTER = 15.0
# An untouched session is a closed player: drop it.
_SESSION_IDLE_TTL = 300.0


class LiveHlsError(Exception):
    """The live could not be resolved into playable HLS playlists."""


class LiveHlsSession:
    """One proxied live: rewritten master + per-itag playlists and segments."""

    def __init__(self, video_url: str, max_height: int) -> None:
        self.video_url = video_url
        self.max_height = max_height
        self.entry_text: str | None = None  # rewritten master (or single media)
        self.media_urls: dict[int, str] = {}  # itag -> upstream media playlist
        self.segments: dict[tuple[int, int], str] = {}  # (itag, sq) -> upstream
        self.resolved_at = 0.0
        self.generation = 0
        self.last_used = time.monotonic()
        self.lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None

    # -- resolution ---------------------------------------------------------

    async def _resolve(self, client: httpx.AsyncClient) -> None:
        """Fresh extraction -> master/media playlists -> itag + segment maps."""
        ytdlp.evict_cached_info(self.video_url)
        info = await ytdlp.resolve_streams(self.video_url, max_height=self.max_height)
        if not info.is_live:
            raise LiveHlsError("not a live stream")
        resp = await client.get(info.url)
        resp.raise_for_status()
        text = resp.text
        if "#EXT-X-STREAM-INF" in text:
            self.entry_text = self._rewrite_master(text)
        else:
            # A single media playlist (muxed variant): serve it as the entry,
            # its segments already rewritten to {itag}/{sq}.
            itag = self._itag_of(str(resp.url))
            self.media_urls = {itag: str(resp.url)}
            self.entry_text = None
            self._ingest(itag, text)
        self.resolved_at = time.monotonic()
        self.generation += 1
        log.info(
            "live-hls %s: resolved generation %d (itags %s)",
            self.video_url,
            self.generation,
            sorted(self.media_urls),
        )

    @staticmethod
    def _itag_of(url: str) -> int:
        m = _ITAG_RE.search(url)
        if m is None:
            raise LiveHlsError(f"no itag in playlist URL: {url[:120]}")
        return int(m.group(1))

    def _rewrite_master(self, text: str) -> str:
        """Filter video variants by the cap and point every URI at media/{itag}.

        Audio renditions (``EXT-X-MEDIA``) are all kept — the cap only applies
        to video. When no variant fits the cap, the lowest one stays: a cap
        must never make a live unplayable.
        """
        lines = text.splitlines()
        heights: list[int] = []
        for line in lines:
            if line.startswith("#EXT-X-STREAM-INF"):
                m = _RESOLUTION_RE.search(line)
                heights.append(int(m.group(1)) if m else 0)
        fitting = [h for h in heights if h <= self.max_height]
        floor = min(heights) if heights and not fitting else 0

        media_urls: dict[int, str] = {}

        def _register(upstream: str) -> str:
            itag = self._itag_of(upstream)
            media_urls[itag] = upstream
            return f"media/{itag}.m3u8"

        out: list[str] = []
        skip_next_uri = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#EXT-X-STREAM-INF"):
                m = _RESOLUTION_RE.search(stripped)
                height = int(m.group(1)) if m else 0
                keep = height <= self.max_height if fitting else height == floor
                if not keep:
                    skip_next_uri = True
                    continue
                out.append(line)
            elif stripped and not stripped.startswith("#"):
                if skip_next_uri:
                    skip_next_uri = False
                    continue
                out.append(_register(stripped))
            elif 'URI="' in stripped:
                out.append(
                    _URI_ATTR_RE.sub(lambda m: f'URI="{_register(m.group(1))}"', line)
                )
            else:
                out.append(line)
        if not media_urls:
            raise LiveHlsError("master manifest lists no playable playlists")
        self.media_urls = media_urls
        # Old generations' segment maps die with their URLs.
        self.segments = {}
        return "\n".join(out) + "\n"

    def _ingest(self, itag: int, text: str) -> str:
        """Map (itag, sq) -> upstream URL, rewrite segment lines to {itag}/{sq}."""
        out: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                m = _SQ_RE.search(stripped)
                if m is None:  # not a googlevideo live segment: pass through
                    out.append(line)
                    continue
                sq = int(m.group(1))
                self.segments[(itag, sq)] = stripped
                out.append(f"{itag}/{sq}")
            else:
                out.append(line)
        return "\n".join(out) + "\n"

    async def _reresolve(self, client: httpx.AsyncClient, seen_generation: int) -> None:
        """Re-extract unless another caller already did since ``seen_generation``."""
        async with self.lock:
            if self.generation == seen_generation:
                await self._resolve(client)

    def refresh_soon(self, client: httpx.AsyncClient) -> None:
        """Proactive background re-extraction before the URLs die (no player wait)."""
        if time.monotonic() - self.resolved_at < REFRESH_AFTER:
            return
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        generation = self.generation

        async def _refresh() -> None:
            try:
                await self._reresolve(client, generation)
            except (httpx.HTTPError, ytdlp.UpstreamError, LiveHlsError) as exc:
                log.warning(
                    "live-hls %s: background refresh failed: %s", self.video_url, exc
                )

        self._refresh_task = asyncio.create_task(_refresh())

    # -- serving ------------------------------------------------------------

    async def entry_playlist(self, client: httpx.AsyncClient) -> str:
        """The rewritten master (or single media playlist), resolving as needed."""
        self.last_used = time.monotonic()
        async with self.lock:
            if not self.media_urls:
                await self._resolve(client)
        if self.entry_text is not None:
            self.refresh_soon(client)
            return self.entry_text
        # Single-media live: the entry is the media playlist itself.
        itag = next(iter(self.media_urls))
        return await self.media_playlist(client, itag)

    async def media_playlist(self, client: httpx.AsyncClient, itag: int) -> str:
        """One variant/rendition playlist, rewritten, healing on failure."""
        self.last_used = time.monotonic()
        async with self.lock:
            url = self.media_urls.get(itag)
            if url is None:
                await self._resolve(client)
                url = self.media_urls.get(itag)
                if url is None:
                    raise LiveHlsError(f"itag {itag} not in this live")
            try:
                resp = await client.get(url)
                if resp.status_code >= 400:
                    raise LiveHlsError(f"playlist answered {resp.status_code}")
            except (httpx.HTTPError, LiveHlsError) as exc:
                log.info(
                    "live-hls %s: media playlist %d failed (%s), re-resolving",
                    self.video_url,
                    itag,
                    exc,
                )
                await self._resolve(client)
                url = self.media_urls.get(itag)
                if url is None:
                    raise LiveHlsError(f"itag {itag} gone after re-resolve") from exc
                resp = await client.get(url)
                resp.raise_for_status()
            text = self._ingest(itag, resp.text)
        self.refresh_soon(client)
        return text

    async def segment_upstream(
        self, client: httpx.AsyncClient, itag: int, sq: int
    ) -> str | None:
        """Current upstream URL for a segment, refreshing the map if it lags."""
        self.last_used = time.monotonic()
        url = self.segments.get((itag, sq))
        if url is None and itag in self.media_urls:
            # The player asked for a segment newer than our last playlist read.
            async with self.lock:
                if (itag, sq) not in self.segments:
                    playlist_url = self.media_urls.get(itag)
                    if playlist_url is not None:
                        try:
                            resp = await client.get(playlist_url)
                            if resp.status_code < 400:
                                self._ingest(itag, resp.text)
                        except httpx.HTTPError:
                            pass
            url = self.segments.get((itag, sq))
        if url is None:
            # Outside the window we last saw: rebuild from any known URL of the
            # same itag — /sq/N/ is the only per-segment part that matters.
            template = next(
                (u for (i, _), u in self.segments.items() if i == itag), None
            )
            if template is not None:
                url = _SQ_RE.sub(f"/sq/{sq}/", template)
        return url


_sessions: dict[str, LiveHlsSession] = {}


def get_session(video_url: str, max_height: int | None = None) -> LiveHlsSession:
    """The session for a live, pruning idle ones on the way.

    ``max_height=None`` keeps the session's current cap (media/segment
    requests carry no quality; the entry playlist request set it).
    """
    now = time.monotonic()
    for key, sess in list(_sessions.items()):
        if now - sess.last_used > _SESSION_IDLE_TTL:
            del _sessions[key]
    sess = _sessions.get(video_url)
    if sess is None:
        sess = _sessions[video_url] = LiveHlsSession(video_url, max_height or 1440)
    elif max_height is not None and sess.max_height != max_height:
        # Quality change: next resolution applies the new cap.
        sess.max_height = max_height
        sess.media_urls = {}
        sess.entry_text = None
    sess.last_used = now
    return sess
