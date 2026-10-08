"""Same-origin byte/HLS proxy for the web player.

Browsers cannot fetch upstream CDN URLs through MSE players (hls.js,
dash.js, mpegts.js) or <track> elements because googlevideo & friends send
no CORS headers. This router re-serves those bytes from our own origin;
media requests authenticate with the session cookie (see security.py).
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Callable

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from ..services import livehls, twitch, ytdlp

router = APIRouter()

PROXY_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"

_PASSTHROUGH_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges")
# Tags whose URI="..." attribute points at another playlist (vs raw bytes).
_PLAYLIST_URI_TAGS = ("#EXT-X-MEDIA", "#EXT-X-I-FRAME-STREAM-INF")
_URI_ATTR_RE = re.compile(r'URI="([^"]+)"')
# Proxied bytes are served from OUR origin, where the session cookie grants
# the whole API: a document among them (an HTML/SVG file uploaded to an
# allowlisted CDN) must never run as a page. nosniff + a sandboxing CSP keep
# any response inert when navigated to; they have no effect on media playback.
_BASE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}
# Content types relayed as-is; anything else (text/html, SVG, XML, scripts
# "stored" on a CDN) is downgraded to an opaque download.
_MEDIA_TYPE_PREFIXES = (
    "video/",
    "audio/",
    "image/",
    "text/vtt",
    "text/plain",
    "application/octet-stream",
    "binary/octet-stream",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/mp4",
    "application/dash+xml",
)
_MAX_REDIRECTS = 5


# Media CDNs of the supported platforms. A same-origin proxy without a
# destination allowlist is an open relay: anything else answers 403.
_ALLOWED_SUFFIXES = (
    # YouTube: media, captions, thumbnails
    ".googlevideo.com",
    ".youtube.com",
    ".ytimg.com",
    ".ggpht.com",
    # Twitch: usher playlists + edge CDN
    ".ttvnw.net",
    ".jtvnw.net",
    # TikTok CDNs (regional variants)
    ".tiktokcdn.com",
    ".tiktokcdn-us.com",
    ".tiktokcdn-eu.com",
    ".tiktokv.com",
    # Odysee
    ".odycdn.com",
    ".odysee.com",
    ".lbryplayer.xyz",
    # BitChute (seed*.bitchute.com)
    ".bitchute.com",
    # CrowdBunker
    ".divulg.org",
    ".crowdbunker.com",
)


# CDNs that refuse a request without a Referer: player.odycdn.com answers 401
# to a bare GET and 206 with any Referer (measured 2026-10-08). A browser
# playing the URL directly always sends one; the relay has to as well.
_REFERERS = {".odycdn.com": "https://odysee.com/"}


def _upstream_headers(url: str) -> dict[str, str]:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    for suffix, referer in _REFERERS.items():
        if host == suffix[1:] or host.endswith(suffix):
            return {"Referer": referer}
    return {}


def _extra_domains(settings) -> set[str]:
    """Operator-extended domains plus the configured Twitch playlist proxies."""
    extras = {
        domain.strip().lower().lstrip(".")
        for domain in settings.proxy_allowed_domains.split(",")
        if domain.strip()
    }
    for proxy in settings.twitch_proxies.split(","):
        proxy = proxy.strip()
        if proxy:
            host = urllib.parse.urlparse(proxy).netloc.rsplit("@", 1)[-1].split(":")[0]
            if host:
                extras.add(host.lower())
    return extras


def _check_url(url: str, settings) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http(s) URLs can be proxied")
    host = (parsed.hostname or "").lower()
    if any(host == suffix[1:] or host.endswith(suffix) for suffix in _ALLOWED_SUFFIXES):
        return
    if any(host == extra or host.endswith(f".{extra}") for extra in _extra_domains(settings)):
        return
    raise HTTPException(status_code=403, detail="Domain not allowed through the proxy")


def _is_twitch_proxy_master(url: str, settings) -> bool:
    """A live master playlist served by one of the configured Twitch proxies."""
    parsed = urllib.parse.urlparse(url)
    hosts = {
        (urllib.parse.urlparse(p.strip()).hostname or "").lower()
        for p in settings.twitch_proxies.split(",")
        if p.strip()
    }
    return (parsed.hostname or "").lower() in hosts and parsed.path.startswith("/live/")


def _safe_content_type(value: str) -> str:
    base = value.split(";", 1)[0].strip().lower()
    if base == "image/svg+xml" or not base.startswith(_MEDIA_TYPE_PREFIXES):
        return "application/octet-stream"
    return value


def _relay_headers(upstream: httpx.Response) -> dict[str, str]:
    headers = dict(_BASE_HEADERS)
    for name in _PASSTHROUGH_HEADERS:
        value = upstream.headers.get(name)
        if value:
            headers[name] = _safe_content_type(value) if name == "content-type" else value
    return headers


async def _send_allowlisted(
    client: httpx.AsyncClient, request: httpx.Request, settings, *, stream: bool
) -> httpx.Response:
    """Send, following redirects by hand so every hop passes the allowlist.

    The shared client follows redirects on its own, which would let an
    allowlisted host bounce the proxy anywhere (internal services included).
    """
    for _ in range(_MAX_REDIRECTS + 1):
        response = await client.send(request, stream=stream, follow_redirects=False)
        target = response.next_request
        if target is None:
            return response
        await response.aclose()
        _check_url(str(target.url), settings)
        request = target
    raise HTTPException(status_code=502, detail="Too many upstream redirects")


#: Maps an absolute upstream URL to the URL the player should fetch instead;
#: ``playlist`` tells another m3u8 (to rewrite in turn) from raw bytes.
ProxyUrl = Callable[[str, bool], str]


def _proxied(url: str, playlist: bool) -> str:
    endpoint = "/api/proxy/hls" if playlist else "/api/proxy"
    return f"{endpoint}?url={urllib.parse.quote(url, safe='')}"


def rewrite_hls(text: str, base_url: str, proxied: ProxyUrl = _proxied) -> str:
    """Rewrite every URI in an m3u8 playlist so it goes through this proxy.

    Bare lines are variant playlists or media segments (relative URLs are
    resolved against ``base_url``); ``URI="..."`` attributes cover alternate
    renditions, init sections and keys. ``proxied`` builds the served URLs
    (the share links sign theirs, see routers/share_public.py).
    """
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            absolute = urllib.parse.urljoin(base_url, stripped)
            path = urllib.parse.urlparse(absolute).path
            out.append(proxied(absolute, path.endswith(".m3u8")))
        elif 'URI="' in stripped:
            playlist = stripped.startswith(_PLAYLIST_URI_TAGS)

            def _sub(match: re.Match[str], *, playlist: bool = playlist) -> str:
                absolute = urllib.parse.urljoin(base_url, match.group(1))
                return f'URI="{proxied(absolute, playlist)}"'

            out.append(_URI_ATTR_RE.sub(_sub, line))
        else:
            out.append(line)
    return "\n".join(out) + "\n"


# -- self-healing YouTube live proxy ---------------------------------------
#
# A YouTube live's direct segment URLs die ~26 s after extraction (capped
# anonymous bucket, measured 2026-08-19) while the playlist stays alive: no
# client can play them smoothly. These routes serve a stable playlist whose
# session re-extracts and remaps segments (by their /sq/N/ number) underneath
# the player. See services/livehls.py.

_YT_WATCH = "https://www.youtube.com/watch?v="


def _m3u8(text: str) -> Response:
    return Response(
        content=text,
        media_type="application/vnd.apple.mpegurl",
        headers=dict(_BASE_HEADERS),
    )


_LIVE_ERRORS = (httpx.HTTPError, livehls.LiveHlsError, ytdlp.UpstreamError)


@router.get("/live/{video_id}/playlist.m3u8")
async def live_playlist(
    video_id: str,
    request: Request,
    max_height: int = 1440,
) -> Response:
    # One session per live whatever the cap: two devices watching the same
    # live at different qualities share its extraction and itag map.
    session = livehls.get_session(f"{_YT_WATCH}{video_id}")
    client: httpx.AsyncClient = request.app.state.proxy_client
    try:
        return _m3u8(await session.entry_playlist(client, max_height))
    except _LIVE_ERRORS as exc:
        raise HTTPException(status_code=502, detail=f"Live resolve failed: {exc}") from exc


@router.get("/live/{video_id}/media/{itag}.m3u8")
async def live_media_playlist(video_id: str, itag: int, request: Request) -> Response:
    session = livehls.get_session(f"{_YT_WATCH}{video_id}")
    client: httpx.AsyncClient = request.app.state.proxy_client
    try:
        return _m3u8(await session.media_playlist(client, itag))
    except _LIVE_ERRORS as exc:
        raise HTTPException(status_code=502, detail=f"Live playlist failed: {exc}") from exc


@router.get("/live/{video_id}/media/{itag}/{sq}")
async def live_segment(
    video_id: str, itag: int, sq: int, request: Request
) -> StreamingResponse:
    session = livehls.get_session(f"{_YT_WATCH}{video_id}")
    client: httpx.AsyncClient = request.app.state.proxy_client
    # Two passes: serve from the current map, and on upstream 403 re-extract
    # once and retry with the remapped URL. The player just sees the bytes.
    for attempt in range(2):
        generation = session.generation
        url = await session.segment_upstream(client, itag, sq)
        if url is None:
            raise HTTPException(status_code=404, detail="Unknown live segment")
        req = client.build_request("GET", url, headers={"Accept-Encoding": "identity"})
        try:
            upstream = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Segment fetch failed: {exc}") from exc
        if upstream.status_code == 403 and attempt == 0:
            await upstream.aclose()
            try:
                await session._reresolve(client, generation)
            except _LIVE_ERRORS as exc:
                raise HTTPException(
                    status_code=502, detail=f"Live re-resolve failed: {exc}"
                ) from exc
            continue
        session.refresh_soon(client)
        return StreamingResponse(
            upstream.aiter_bytes(),
            status_code=upstream.status_code,
            headers=_relay_headers(upstream),
            background=BackgroundTask(upstream.aclose),
        )
    raise HTTPException(status_code=502, detail="Live segment kept answering 403")


@router.get("/proxy")
async def proxy_bytes(url: str, request: Request) -> StreamingResponse:
    """Stream upstream bytes as-is, forwarding Range for seekable media."""
    return await relay_bytes(url, request)


@router.get("/proxy/hls")
async def proxy_hls(url: str, request: Request) -> Response:
    """Fetch an m3u8 playlist and rewrite its URIs through the proxy."""
    return await relay_playlist(url, request)


async def relay_bytes(url: str, request: Request) -> StreamingResponse:
    """Relay one allowlisted upstream resource (shared with the share links)."""
    settings = request.app.state.settings
    _check_url(url, settings)
    client: httpx.AsyncClient = request.app.state.proxy_client
    upstream_headers = {"Accept-Encoding": "identity", **_upstream_headers(url)}
    range_header = request.headers.get("Range")
    if range_header:
        upstream_headers["Range"] = range_header
    req = client.build_request("GET", url, headers=upstream_headers)
    try:
        upstream = await _send_allowlisted(client, req, settings, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Proxy fetch failed: {exc}") from exc
    if upstream.status_code == 403:
        # A URL that was served then starts answering 403 is dead for good
        # (PO-token bucket, expiry): drop the cached extraction so the
        # player's retry re-resolves instead of replaying the same corpse.
        ytdlp.forget_dead_stream(url)
    return StreamingResponse(
        upstream.aiter_bytes(),
        status_code=upstream.status_code,
        headers=_relay_headers(upstream),
        background=BackgroundTask(upstream.aclose),
    )


async def relay_playlist(
    url: str, request: Request, proxied: ProxyUrl = _proxied
) -> Response:
    """Fetch an allowlisted m3u8 and rewrite its URIs with ``proxied``."""
    settings = request.app.state.settings
    _check_url(url, settings)
    client: httpx.AsyncClient = request.app.state.proxy_client
    if _is_twitch_proxy_master(url, settings):
        # The player's master is a new token, hence a new ad draw (the one
        # probed by /streams told nothing about it): redraw past a preroll.
        master, _clean = await twitch.fetch_ad_free_master(
            client, url, attempts=twitch.AD_FREE_ATTEMPTS + 1
        )
        if master is None:
            raise HTTPException(status_code=502, detail="Twitch playlist proxy failed")
        return Response(
            content=rewrite_hls(master, url, proxied),
            media_type="application/vnd.apple.mpegurl",
            headers=dict(_BASE_HEADERS),
        )
    try:
        upstream = await _send_allowlisted(
            client,
            client.build_request("GET", url, headers=_upstream_headers(url)),
            settings,
            stream=False,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Proxy fetch failed: {exc}") from exc
    if upstream.status_code >= 400:
        if upstream.status_code == 403:
            ytdlp.forget_dead_stream(url)
        raise HTTPException(
            status_code=upstream.status_code, detail="Upstream playlist error"
        )
    return Response(
        content=rewrite_hls(upstream.text, str(upstream.url), proxied),
        media_type="application/vnd.apple.mpegurl",
        headers=dict(_BASE_HEADERS),
    )
