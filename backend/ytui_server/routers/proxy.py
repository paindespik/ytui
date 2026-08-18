"""Same-origin byte/HLS proxy for the web player.

Browsers cannot fetch upstream CDN URLs through MSE players (hls.js,
dash.js, mpegts.js) or <track> elements because googlevideo & friends send
no CORS headers. This router re-serves those bytes from our own origin;
media requests authenticate with the session cookie (see security.py).
"""

from __future__ import annotations

import re
import urllib.parse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from ..services import ytdlp

router = APIRouter()

PROXY_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"

_PASSTHROUGH_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges")
# Tags whose URI="..." attribute points at another playlist (vs raw bytes).
_PLAYLIST_URI_TAGS = ("#EXT-X-MEDIA", "#EXT-X-I-FRAME-STREAM-INF")
_URI_ATTR_RE = re.compile(r'URI="([^"]+)"')
_BASE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


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


def _proxied(url: str, *, playlist: bool) -> str:
    endpoint = "/api/proxy/hls" if playlist else "/api/proxy"
    return f"{endpoint}?url={urllib.parse.quote(url, safe='')}"


def rewrite_hls(text: str, base_url: str) -> str:
    """Rewrite every URI in an m3u8 playlist so it goes through this proxy.

    Bare lines are variant playlists or media segments (relative URLs are
    resolved against ``base_url``); ``URI="..."`` attributes cover alternate
    renditions, init sections and keys.
    """
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            absolute = urllib.parse.urljoin(base_url, stripped)
            path = urllib.parse.urlparse(absolute).path
            out.append(_proxied(absolute, playlist=path.endswith(".m3u8")))
        elif 'URI="' in stripped:
            playlist = stripped.startswith(_PLAYLIST_URI_TAGS)

            def _sub(match: re.Match[str], *, playlist: bool = playlist) -> str:
                absolute = urllib.parse.urljoin(base_url, match.group(1))
                return f'URI="{_proxied(absolute, playlist=playlist)}"'

            out.append(_URI_ATTR_RE.sub(_sub, line))
        else:
            out.append(line)
    return "\n".join(out) + "\n"


@router.get("/proxy")
async def proxy_bytes(url: str, request: Request) -> StreamingResponse:
    """Stream upstream bytes as-is, forwarding Range for seekable media."""
    _check_url(url, request.app.state.settings)
    client: httpx.AsyncClient = request.app.state.proxy_client
    upstream_headers = {"Accept-Encoding": "identity"}
    range_header = request.headers.get("Range")
    if range_header:
        upstream_headers["Range"] = range_header
    req = client.build_request("GET", url, headers=upstream_headers)
    try:
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Proxy fetch failed: {exc}") from exc
    if upstream.status_code == 403:
        # A URL that was served then starts answering 403 is dead for good
        # (PO-token bucket, expiry): drop the cached extraction so the
        # player's retry re-resolves instead of replaying the same corpse.
        ytdlp.forget_dead_stream(url)
    headers = dict(_BASE_HEADERS)
    for name in _PASSTHROUGH_HEADERS:
        value = upstream.headers.get(name)
        if value:
            headers[name] = value
    return StreamingResponse(
        upstream.aiter_bytes(),
        status_code=upstream.status_code,
        headers=headers,
        background=BackgroundTask(upstream.aclose),
    )


@router.get("/proxy/hls")
async def proxy_hls(url: str, request: Request) -> Response:
    """Fetch an m3u8 playlist and rewrite its URIs through the proxy."""
    _check_url(url, request.app.state.settings)
    client: httpx.AsyncClient = request.app.state.proxy_client
    try:
        upstream = await client.get(url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Proxy fetch failed: {exc}") from exc
    if upstream.status_code >= 400:
        if upstream.status_code == 403:
            ytdlp.forget_dead_stream(url)
        raise HTTPException(
            status_code=upstream.status_code, detail="Upstream playlist error"
        )
    return Response(
        content=rewrite_hls(upstream.text, str(upstream.url)),
        media_type="application/vnd.apple.mpegurl",
        headers=dict(_BASE_HEADERS),
    )
