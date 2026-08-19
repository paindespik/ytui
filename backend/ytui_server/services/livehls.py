"""Self-healing HLS proxy sessions for YouTube lives.

YouTube currently enrols every anonymous session of this server's IP in a
bucket whose live *segment* URLs stop being served ~26 s after extraction
(measured 2026-08-19: 200 at +14 s, 403 from +26 s on, every video variant at
once, while the playlist URL keeps answering 200 indefinitely and the
audio-only variant survives). No client-side buffering can ride that out, so
clients gag on a frozen picture every ~75 s.

The cure lives here: the client plays a *stable* playlist served by this
process. Each refresh re-reads the upstream playlist, maps segment sequence
numbers (the ``/sq/N/`` path element, stable across extractions) to their
current upstream URLs, and rewrites the playlist to relative ``seg/N`` URIs.
When an upstream segment answers 403 — or ages past the measured lifetime —
the session re-extracts the live and remaps the same sequence numbers onto the
fresh URLs. The player never sees the swap: its buffer keeps draining while
the session heals underneath.
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
_RESOLUTION_RE = re.compile(r"RESOLUTION=\d+x(\d+)")

# Segment URLs die ~26 s after extraction; start refreshing well before that
# so a fresh map is ready without the player ever hitting a 403.
REFRESH_AFTER = 15.0
# An untouched session is a closed player: drop it.
_SESSION_IDLE_TTL = 300.0


class LiveHlsError(Exception):
    """The live could not be resolved into a playable HLS media playlist."""


def _pick_variant(master: str, max_height: int) -> str | None:
    """Best variant at or below the cap; the lowest available otherwise."""
    best: tuple[int, str] | None = None
    lowest: tuple[int, str] | None = None
    lines = master.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        m = _RESOLUTION_RE.search(line)
        height = int(m.group(1)) if m else 0
        url = next(
            (ln.strip() for ln in lines[i + 1 :] if ln.strip() and not ln.startswith("#")),
            None,
        )
        if url is None:
            continue
        if lowest is None or height < lowest[0]:
            lowest = (height, url)
        if height <= max_height and (best is None or height > best[0]):
            best = (height, url)
    chosen = best or lowest
    return chosen[1] if chosen else None


class LiveHlsSession:
    """One proxied live: current upstream playlist + sq -> URL segment map."""

    def __init__(self, video_url: str, max_height: int) -> None:
        self.video_url = video_url
        self.max_height = max_height
        self.playlist_url: str | None = None
        self.playlist_text = ""  # rewritten, ready to serve
        self.segments: dict[int, str] = {}
        self.resolved_at = 0.0
        self.generation = 0
        self.last_used = time.monotonic()
        self.lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None

    # -- resolution ---------------------------------------------------------

    async def _resolve(self, client: httpx.AsyncClient) -> None:
        """Fresh extraction -> media playlist for the cap -> segment map."""
        ytdlp.evict_cached_info(self.video_url)
        info = await ytdlp.resolve_streams(self.video_url, max_height=self.max_height)
        if not info.is_live:
            raise LiveHlsError("not a live stream")
        url = info.url
        resp = await client.get(url)
        resp.raise_for_status()
        text = resp.text
        if "#EXT-X-STREAM-INF" in text:  # master manifest: pick one variant
            variant = _pick_variant(text, self.max_height)
            if variant is None:
                raise LiveHlsError("master manifest lists no variants")
            resp = await client.get(variant)
            resp.raise_for_status()
            text = resp.text
        self.playlist_url = str(resp.url)
        self.resolved_at = time.monotonic()
        self.generation += 1
        self._ingest(text)
        log.info(
            "live-hls %s: resolved generation %d (%d segments)",
            self.video_url,
            self.generation,
            len(self.segments),
        )

    def _ingest(self, text: str) -> None:
        """Map sq -> upstream URL and rewrite segment lines to ``seg/N``."""
        out: list[str] = []
        segments: dict[int, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                m = _SQ_RE.search(stripped)
                if m is None:
                    # Not a googlevideo live segment: pass through untouched
                    # (defensive; should not happen on a YouTube live).
                    out.append(line)
                    continue
                sq = int(m.group(1))
                segments[sq] = stripped
                out.append(f"seg/{sq}")
            else:
                out.append(line)
        self.segments = segments
        self.playlist_text = "\n".join(out) + "\n"

    async def _reresolve(self, client: httpx.AsyncClient, seen_generation: int) -> None:
        """Re-extract unless another caller already did since ``seen_generation``."""
        async with self.lock:
            if self.generation == seen_generation:
                await self._resolve(client)

    def _refresh_soon(self, client: httpx.AsyncClient) -> None:
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
                log.warning("live-hls %s: background refresh failed: %s", self.video_url, exc)

        self._refresh_task = asyncio.create_task(_refresh())

    # -- serving ------------------------------------------------------------

    async def playlist(self, client: httpx.AsyncClient) -> str:
        """The rewritten media playlist, resolving/healing as needed."""
        self.last_used = time.monotonic()
        async with self.lock:
            if self.playlist_url is None:
                await self._resolve(client)
                return self.playlist_text
            try:
                resp = await client.get(self.playlist_url)
                if resp.status_code >= 400:
                    raise LiveHlsError(f"playlist answered {resp.status_code}")
                self._ingest(resp.text)
            except (httpx.HTTPError, LiveHlsError) as exc:
                log.info("live-hls %s: playlist refetch failed (%s), re-resolving", self.video_url, exc)
                await self._resolve(client)
        self._refresh_soon(client)
        return self.playlist_text

    async def segment_upstream(self, client: httpx.AsyncClient, sq: int) -> str | None:
        """Current upstream URL for a sequence number, refreshing the map if stale."""
        self.last_used = time.monotonic()
        url = self.segments.get(sq)
        if url is None and self.playlist_url is not None:
            # The map lags the player (it asked for a segment newer than our
            # last playlist read): refetch the playlist under the lock.
            async with self.lock:
                if sq not in self.segments and self.playlist_url is not None:
                    try:
                        resp = await client.get(self.playlist_url)
                        if resp.status_code < 400:
                            self._ingest(resp.text)
                    except httpx.HTTPError:
                        pass
            url = self.segments.get(sq)
        if url is None and self.segments:
            # Older than the window we last saw: rebuild from any known URL —
            # the /sq/N/ path element is the only per-segment part that matters.
            template = next(iter(self.segments.values()))
            url = _SQ_RE.sub(f"/sq/{sq}/", template)
        return url


_sessions: dict[str, LiveHlsSession] = {}


def get_session(video_url: str, max_height: int | None = None) -> LiveHlsSession:
    """The session for a live, pruning idle ones on the way.

    ``max_height=None`` keeps the session's current cap (segment requests
    carry no quality; the playlist request set it).
    """
    now = time.monotonic()
    for key, sess in list(_sessions.items()):
        if now - sess.last_used > _SESSION_IDLE_TTL:
            del _sessions[key]
    sess = _sessions.get(video_url)
    if sess is None:
        sess = _sessions[video_url] = LiveHlsSession(video_url, max_height or 1440)
    elif max_height is not None and sess.max_height != max_height:
        # Quality change: next resolution picks the new cap.
        sess.max_height = max_height
        sess.playlist_url = None
    sess.last_used = now
    return sess
