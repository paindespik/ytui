"""Public side of share links (/s/{token}): no API token, one video only.

The page is a standalone player (web/js/share.js); its media routes replay
the web player's pipeline scoped to the share's video:

- /streams resolves the share's own video (never a requested one) and returns
  ordered sources whose upstream URLs are signed for this token;
- /p and /hls relay ONLY signed URLs, re-signing every URI of the playlists
  they rewrite, so nothing reachable from here leads to another video;
- /live/... is the self-healing YouTube live proxy, keyed by the share row;
- /mpd and /sponsor answer for the share's video too.

Each request re-reads the share row: expiry or revocation cuts everything.
"""

from __future__ import annotations

import html
import string
import urllib.parse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from ..db import ShareRow
from ..models import ShareSource, ShareStreamsOut, SponsorSegmentsOut, StreamInfo
from ..services import share
from . import proxy, videos
from .shares import share_video

router = APIRouter()

#: Default quality cap of a share: the bytes go out through this server.
DEFAULT_MAX_HEIGHT = 1080

_Height = Query(default=DEFAULT_MAX_HEIGHT, ge=144, le=4320)

# The page shares the origin of the signed-in web app: no inline script,
# nothing but same-origin code (hls.js workers run from blob: URLs, MSE
# plays from blob: URLs), and the token-bearing URL never leaks as Referer.
_PAGE_HEADERS = {
    "Content-Security-Policy": "; ".join(
        (
            "default-src 'none'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' https: data:",
            "media-src 'self' blob:",
            "connect-src 'self'",
            "worker-src 'self' blob:",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
        )
    ),
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}

_PAGE = string.Template(
    """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#101014">
  <meta name="robots" content="noindex, nofollow">
  <meta name="referrer" content="no-referrer">
  <title>$title</title>
  <meta property="og:type" content="video.other">
  <meta property="og:site_name" content="ytui">
  <meta property="og:title" content="$title">
  <meta property="og:description" content="$description">
  $og_image
  <meta name="twitter:card" content="summary_large_image">
  <link rel="icon" href="/icon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/css/share.css">
  <script type="module" src="/js/share.js"></script>
</head>
<body data-token="$token" data-platform="$platform" data-expires="$expires">
  <main class="share">
    <div class="stage">
      <video id="video" controls playsinline preload="none"$poster></video>
      <div class="message" id="message" hidden></div>
    </div>
    <section class="info">
      <h1 id="title">$title</h1>
      <p class="channel">$channel</p>
      <div class="controls">
        <label>Qualité <select id="quality"></select></label>
        <label id="subs-label" hidden>Sous-titres <select id="subs"></select></label>
        <span class="served" id="served"></span>
        <span class="grow"></span>
        <span class="skip" id="skip" role="status" aria-live="polite"></span>
      </div>
    </section>
    <footer id="footer">Lien privé partagé via ytui</footer>
  </main>
</body>
</html>
"""
)

_GONE_PAGE = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <meta name="referrer" content="no-referrer">
  <title>Lien expiré · ytui</title>
  <link rel="icon" href="/icon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/css/share.css">
</head>
<body>
  <main class="share gone">
    <h1>Ce lien n'est plus valable</h1>
    <p>Il a expiré ou a été révoqué. Demandez un nouveau lien à la personne qui vous l'a envoyé.</p>
  </main>
</body>
</html>
"""


def _active_share(request: Request, token: str) -> ShareRow:
    row = request.app.state.db.get_share(token)
    if row is None or row.expired():
        raise HTTPException(status_code=404, detail="Share link not found or expired")
    return row


def _signer(request: Request, token: str) -> proxy.ProxyUrl:
    api_token: str = request.app.state.settings.api_token

    def signed(url: str, playlist: bool) -> str:
        return share.signed_url(api_token, token, url, playlist)

    return signed


def _check_signature(request: Request, token: str, url: str, expires: int, sig: str) -> None:
    api_token: str = request.app.state.settings.api_token
    if not share.verify(api_token, token, url, expires, sig):
        raise HTTPException(status_code=403, detail="Invalid or expired signature")


def _thumbnail(row: ShareRow) -> str:
    url = row.thumbnail_url
    if not url and row.platform == "youtube":
        url = f"https://i.ytimg.com/vi/{row.video_id}/hqdefault.jpg"
    # Only plain https images: the URL is embedded in the page.
    return url if url.startswith("https://") else ""


def render_page(row: ShareRow) -> str:
    esc = html.escape  # titles come from uploaders: everything is escaped
    image = _thumbnail(row)
    description = (
        f"{row.channel_title} · partagé via ytui" if row.channel_title else "Partagé via ytui"
    )
    return _PAGE.substitute(
        title=esc(row.title or "Vidéo partagée"),
        description=esc(description),
        channel=esc(row.channel_title),
        og_image=f'<meta property="og:image" content="{esc(image)}">' if image else "",
        poster=f' poster="{esc(image)}"' if image else "",
        token=esc(row.token),
        platform=esc(row.platform),
        expires=str(int(row.expires_at)) if row.expires_at is not None else "",
    )


@router.get("/s/{token}", response_class=HTMLResponse, include_in_schema=False)
async def share_page(token: str, request: Request) -> HTMLResponse:
    # Link-preview bots fetch this page: it resolves nothing upstream, the
    # player script does once a person opens it.
    row = request.app.state.db.get_share(token)
    if row is None or row.expired():
        return HTMLResponse(_GONE_PAGE, status_code=404, headers=_PAGE_HEADERS)
    return HTMLResponse(render_page(row), headers=_PAGE_HEADERS)


async def _ensure_same_live(row: ShareRow) -> None:
    """A Twitch/TikTok live link plays the pinned stream, never the next one."""
    login = row.video_id.split(":", 1)[0]
    try:
        live = await share.current_live(row.platform, login)
    except share.LiveCheckError as exc:
        raise HTTPException(status_code=502, detail=f"Live check failed: {exc}") from exc
    if live is None or live.video_id != row.video_id:
        raise HTTPException(status_code=410, detail="This live has ended")


async def _resolve(row: ShareRow, request: Request, max_height: int, sub_langs: str) -> StreamInfo:
    return await videos.video_streams(
        row.video_id,
        request,
        platform=row.platform,  # type: ignore[arg-type]
        max_height=max_height,
        audio_only=False,
        sub_langs=sub_langs,
    )


@router.get("/s/{token}/streams", response_model=ShareStreamsOut)
async def share_streams(
    token: str,
    request: Request,
    max_height: int = _Height,
    sub_langs: str = Query(default="", max_length=64),
) -> ShareStreamsOut:
    """Playable sources of the shared video, in the order the player tries them
    (same engine choice as the web player, see web/js/player.js)."""
    row = _active_share(request, token)
    channel_live = share.is_channel_live(row.platform, row.video_id)
    if channel_live:
        await _ensure_same_live(row)
    info = await _resolve(row, request, max_height, sub_langs)
    sign = _signer(request, token)
    base = f"/s/{token}"
    sources: list[ShareSource] = []
    if info.kind == "hls" and info.proxy_path:
        # YouTube live: the self-healing proxy, keyed by the share row.
        sources.append(
            ShareSource(engine="hls", url=f"{base}/live/playlist.m3u8?max_height={max_height}")
        )
    elif info.kind == "hls":
        # A FLV-only TikTok room arrives as kind=hls with a .flv URL.
        flv = urllib.parse.urlparse(info.url).path.endswith(".flv")
        sources.append(
            ShareSource(engine="flv" if flv else "hls", url=sign(info.url, not flv))
        )
    else:
        if not info.is_live and (row.platform == "youtube" or info.kind == "split"):
            # Only path above 360p for a YouTube VOD in a browser.
            sources.append(ShareSource(engine="dash", url=f"{base}/mpd?max_height={max_height}"))
        progressive: StreamInfo | None = info
        if info.kind == "split":
            # Split pair without a usable MPD: degrade to a 360p progressive.
            try:
                progressive = await _resolve(row, request, 360, "")
            except HTTPException:
                progressive = None
        if progressive is not None and progressive.kind == "progressive":
            sources.append(ShareSource(engine="progressive", url=sign(progressive.url, False)))
    if not sources:
        raise HTTPException(status_code=502, detail="No playable stream for this video")
    return ShareStreamsOut(
        title=info.title or row.title,
        duration=info.duration,
        height=info.height,
        is_live=info.is_live or channel_live,
        sources=sources,
        subtitles=[
            track.model_copy(update={"url": sign(track.url, False)})
            for track in info.subtitles
        ],
    )


@router.get("/s/{token}/mpd")
async def share_mpd(token: str, request: Request, max_height: int = _Height) -> Response:
    row = _active_share(request, token)
    if share.is_channel_live(row.platform, row.video_id):
        # Never offered for a live; resolving it would bypass the live pin.
        raise HTTPException(status_code=404, detail="No DASH manifest for a live")
    sign = _signer(request, token)
    mpd_xml = await videos.render_mpd(
        share_video(row).url,
        max_height,
        request.app.state.proxy_client,
        lambda url: sign(url, False),
    )
    return Response(
        content=mpd_xml,
        media_type="application/dash+xml",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/s/{token}/p")
async def share_bytes(
    token: str, request: Request, u: str, e: int, s: str
) -> StreamingResponse:
    _active_share(request, token)
    _check_signature(request, token, u, e, s)
    return await proxy.relay_bytes(u, request)


def _is_channel_master(url: str, request: Request) -> bool:
    """A Twitch master playlist addressed by channel, not by stream: fetched
    again later, it serves whatever the channel is streaming by then."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    settings = request.app.state.settings
    return host == "usher.ttvnw.net" or proxy._is_twitch_proxy_master(url, settings)


@router.get("/s/{token}/hls")
async def share_playlist(token: str, request: Request, u: str, e: int, s: str) -> Response:
    row = _active_share(request, token)
    _check_signature(request, token, u, e, s)
    if share.is_channel_live(row.platform, row.video_id) and _is_channel_master(u, request):
        # A signed master stays valid for hours: re-check the pin so it can't
        # open the channel's next live.
        await _ensure_same_live(row)
    return await proxy.relay_playlist(u, request, _signer(request, token))


def _youtube_share(request: Request, token: str) -> ShareRow:
    row = _active_share(request, token)
    if row.platform != "youtube":
        raise HTTPException(status_code=404, detail="Not a YouTube share")
    return row


@router.get("/s/{token}/live/playlist.m3u8")
async def share_live_playlist(
    token: str, request: Request, max_height: int = _Height
) -> Response:
    row = _youtube_share(request, token)
    return await proxy.live_playlist(row.video_id, request, max_height)


@router.get("/s/{token}/live/media/{itag}.m3u8")
async def share_live_media(token: str, itag: int, request: Request) -> Response:
    row = _youtube_share(request, token)
    return await proxy.live_media_playlist(row.video_id, itag, request)


@router.get("/s/{token}/live/media/{itag}/{sq}")
async def share_live_segment(
    token: str, itag: int, sq: int, request: Request
) -> StreamingResponse:
    row = _youtube_share(request, token)
    return await proxy.live_segment(row.video_id, itag, sq, request)


@router.get("/s/{token}/sponsor", response_model=SponsorSegmentsOut)
async def share_sponsor(token: str, request: Request) -> SponsorSegmentsOut:
    row = _active_share(request, token)
    if row.platform != "youtube":
        return SponsorSegmentsOut()
    return SponsorSegmentsOut(segments=await request.app.state.sponsorblock.get(row.video_id))
