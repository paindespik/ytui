"""Odysee (LBRY) service: Lighthouse search + SDK proxy resolve/claim_search,
Commentron comments (read-only), and direct details/stream resolution.
All calls are plain HTTP JSON — no yt-dlp here.

Video identity: video_id = "{name}:{claim_id}", channel_id = "@name:claim_id".
Details/streams go through the SDK proxy below; yt-dlp's LBRY extractor stays
as a fallback only (its api.lbry.tv "get" takes 10-30 s where this proxy
answers in ~200 ms).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

import httpx

from ..models import CommentOut, CommentsResponse, Video, VideoDetails

LIGHTHOUSE_URL = "https://lighthouse.odysee.tv/search"
PROXY_URL = "https://api.na-backend.odysee.com/api/v1/proxy"
COMMENTS_URL = "https://comments.odysee.tv/api/v2"

TIMEOUT = httpx.Timeout(10.0)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ytui-server/0.2"
_HEADERS = {"User-Agent": USER_AGENT}
# Odysee channels also publish images, PDFs and text posts; yt-dlp's LBRY
# extractor only handles video/audio and fails these with "This stream is not
# live", so they are filtered out of every listing.
PLAYABLE_STREAM_TYPES = ("video", "audio")

log = logging.getLogger(__name__)


class OdyseeError(Exception):
    """An Odysee upstream call failed."""


def claim_id_from_video_id(video_id: str) -> str:
    """Extract the claim_id from a 'name:claim_id' video id (or return as-is)."""
    return video_id.rsplit(":", 1)[-1] if ":" in video_id else video_id


async def _proxy_call(client: httpx.AsyncClient, method: str, params: dict) -> dict:
    """JSON-RPC call against the Odysee SDK proxy."""
    resp = await client.post(
        PROXY_URL,
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        headers=_HEADERS,
    )
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise OdyseeError(f"{method}: {body['error']}")
    return body.get("result") or {}


def _is_playable(value: dict[str, Any]) -> bool:
    """True when a stream claim carries playable video/audio media."""
    declared = value.get("stream_type") or ""
    if declared:
        return declared in PLAYABLE_STREAM_TYPES
    media_type = ((value.get("source") or {}).get("media_type") or "")
    return media_type.startswith(("video/", "audio/"))


def _claim_to_video(claim: dict) -> Video | None:
    """Map a resolved/claim_search claim dict to a Video."""
    claim_id = claim.get("claim_id") or ""
    name = claim.get("name") or ""
    if not claim_id or not name:
        return None
    value: dict[str, Any] = claim.get("value") or {}
    value_type = claim.get("value_type") or ""
    if value_type == "channel" or name.startswith("@"):
        kind = "channel"
        video_id = f"{name}:{claim_id}"
        channel_id = video_id
        channel_title = value.get("title") or name
    else:
        if not _is_playable(value):
            return None  # image / document / binary post, or a source-less livestream
        kind = "video"
        video_id = f"{name}:{claim_id}"
        signing = claim.get("signing_channel") or {}
        ch_name = signing.get("name") or ""
        ch_claim = signing.get("claim_id") or ""
        channel_id = f"{ch_name}:{ch_claim}" if ch_name and ch_claim else ""
        channel_title = (signing.get("value") or {}).get("title") or ch_name
    published = None
    release = value.get("release_time") or claim.get("timestamp")
    if release:
        try:
            published = datetime.fromtimestamp(int(release), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            published = None
    duration = (value.get("video") or value.get("audio") or {}).get("duration")
    thumb = (value.get("thumbnail") or {}).get("url") or ""
    return Video(
        video_id=video_id,
        title=value.get("title") or name,
        channel_title=channel_title,
        channel_id=channel_id,
        published=published,
        duration=int(duration) if duration else None,
        thumbnail_url=thumb,
        kind=kind,
        platform="odysee",
    )


async def search(
    query: str, limit: int = 20, offset: int = 0, channel_id: str | None = None
) -> list[Video]:
    """Search Odysee: Lighthouse for hits, batch resolve for full metadata.

    `channel_id` ('@name:claim' or a bare claim id) scopes the search to a single
    channel — claim_search has no usable full-text filter, Lighthouse does.
    """
    if len(query.strip()) < 3:
        # Lighthouse rejects queries shorter than 3 characters (400); there is
        # no upstream alternative, so short global searches simply come back
        # empty instead of failing. In-channel searches fall back to a local
        # title scan (see the videos router) before reaching this path.
        return []
    # Lighthouse caps `size` at 50 (400 above that), so bigger requests are
    # walked in chunks of at most 50 using `from`.
    hits: list[dict] = []
    remaining = limit
    pos = offset
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            while remaining > 0:
                size = min(remaining, 50)
                params: dict[str, Any] = {
                    "s": query,
                    "size": size,
                    "nsfw": "false",
                }
                if pos:
                    params["from"] = pos
                if channel_id:
                    params["channel_id"] = claim_id_from_video_id(channel_id)
                resp = await client.get(
                    LIGHTHOUSE_URL,
                    params=params,
                    headers=_HEADERS,
                )
                resp.raise_for_status()
                chunk = resp.json()
                if not isinstance(chunk, list) or not chunk:
                    break
                hits.extend(chunk)
                pos += len(chunk)
                remaining -= len(chunk)
                if len(chunk) < size:
                    break  # short page: no more results
            if not hits:
                return []
            urls = [
                f"lbry://{h['name']}#{h['claimId']}"
                for h in hits
                if h.get("name") and h.get("claimId")
            ]
            if not urls:
                return []
            result = await _proxy_call(client, "resolve", {"urls": urls})
    except OdyseeError:
        raise
    except Exception as exc:
        log.warning("odysee search failed: %s", exc)
        raise OdyseeError(f"search failed: {exc}") from exc
    videos: list[Video] = []
    for url in urls:
        claim = result.get(url)
        if not isinstance(claim, dict) or "error" in claim:
            continue
        video = _claim_to_video(claim)
        if video:
            videos.append(video)
    return videos


async def channel_videos(channel_id: str, limit: int = 50, offset: int = 0) -> list[Video]:
    """Videos of a channel via claim_search, newest first (channel_id = '@name:claim').

    claim_search is page-based (page_size caps at 50) with no cursor, and a few
    claims are still dropped locally (non-playable ones the filters let through),
    so raw claim indices drift from video indices. `offset` counts *videos*: pages
    are walked from the first and only kept videos are counted, which keeps the
    window exact across 'load more' calls at the cost of re-walking earlier pages.
    """
    claim_id = claim_id_from_video_id(channel_id)
    page_size = 50
    videos: list[Video] = []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            page = 1
            while len(videos) < offset + limit:
                result = await _proxy_call(
                    client,
                    "claim_search",
                    {
                        "channel_ids": [claim_id],
                        "claim_type": ["stream"],
                        "order_by": ["release_time"],
                        "page": page,
                        "page_size": page_size,
                        "has_source": True,
                        "stream_types": list(PLAYABLE_STREAM_TYPES),
                        "no_totals": True,
                    },
                )
                items = result.get("items") or []
                for claim in items:
                    video = _claim_to_video(claim)
                    if video:
                        videos.append(video)
                if len(items) < page_size:
                    break
                page += 1
    except OdyseeError:
        raise
    except Exception as exc:
        log.warning("odysee channel listing failed for %s: %s", channel_id, exc)
        raise OdyseeError(f"channel listing failed: {exc}") from exc
    return videos[offset : offset + limit]


async def resolve_channel(
    handle: str, client: httpx.AsyncClient | None = None
) -> tuple[str, str]:
    """Resolve a bare Odysee channel handle ('@name') to ('@name:claim_id', title).

    Odysee channel refs need the claim_id; users only know the handle. This
    resolves 'lbry://@name' via the SDK proxy to the canonical id and title.
    """
    name = handle if handle.startswith("@") else f"@{handle}"
    url = f"lbry://{name}"
    owns_client = client is None
    try:
        if client is None:
            client = httpx.AsyncClient(timeout=TIMEOUT)
        try:
            result = await _proxy_call(client, "resolve", {"urls": [url]})
        finally:
            if owns_client:
                await client.aclose()
    except OdyseeError:
        raise
    except Exception as exc:
        log.warning("odysee channel resolve failed for %s: %s", name, exc)
        raise OdyseeError(f"channel resolve failed: {exc}") from exc
    claim = result.get(url)
    if not isinstance(claim, dict) or "error" in claim:
        raise OdyseeError(f"Unknown Odysee channel {name!r}")
    claim_id = claim.get("claim_id") or ""
    resolved_name = claim.get("name") or name
    if claim.get("value_type") != "channel" or not claim_id:
        raise OdyseeError(f"{name!r} is not an Odysee channel")
    title = (claim.get("value") or {}).get("title") or resolved_name
    return f"{resolved_name}:{claim_id}", title


class NotPlayableError(OdyseeError):
    """The claim exists but is not a playable video (image, document, …)."""


# ─── Direct details / stream resolution (no yt-dlp) ───
#
# yt-dlp's LBRY extractor resolves through api.lbry.tv, whose JSON-RPC "get"
# was measured at 1-30 s in production (and once hung > 240 s), while the same
# call against the SDK proxy below answers in ~200 ms. Both details and
# streams therefore go through the proxy, and yt-dlp remains a fallback for
# claims the proxy cannot serve.

_STREAM_CACHE_TTL = 6 * 3600.0  # CDN URLs are content-hashed and do not expire
_STREAM_CACHE_MAX = 128
_STREAM_CACHE: dict[str, tuple[float, OdyseeStream]] = {}
# The CDN rejects a bare HEAD without a referer (401); GETs do not need it.
_CDN_HEADERS = {"Referer": "https://odysee.com/"}


def _lbry_uri(video_id: str) -> str:
    """'name:claim' -> 'lbry://name#claim' (bare claim -> 'lbry://x#claim')."""
    if ":" in video_id:
        name, claim = video_id.rsplit(":", 1)
    else:
        name, claim = "x", video_id
    return f"lbry://{name}#{claim}"


async def _resolve_claim(client: httpx.AsyncClient, video_id: str) -> dict:
    """Full claim for a video id via proxy `resolve` ({} when unknown)."""
    uri = _lbry_uri(video_id)
    result = await _proxy_call(client, "resolve", {"urls": [uri]})
    claim = result.get(uri)
    return claim if isinstance(claim, dict) and "error" not in claim else {}


async def video_details(video_id: str) -> VideoDetails:
    """Video details via the proxy `resolve` (~200 ms; no yt-dlp).

    Raises NotPlayableError for known non-media claims so the router can
    answer 415 instead of the old yt-dlp 'This stream is not live'.
    """
    uri = _lbry_uri(video_id)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=_HEADERS) as client:
            result = await _proxy_call(client, "resolve", {"urls": [uri]})
    except Exception as exc:
        log.warning("odysee details resolve failed for %s: %s", video_id, exc)
        raise OdyseeError(f"details resolve failed: {exc}") from exc
    claim = result.get(uri)
    if not isinstance(claim, dict) or "error" in claim:
        raise OdyseeError(f"Unknown Odysee video {video_id!r}")
    value = claim.get("value") or {}
    if not _is_playable(value):
        raise NotPlayableError(
            f"not a playable video ({value.get('stream_type') or 'unknown type'})"
        )
    signing = claim.get("signing_channel") or {}
    ch_name = signing.get("name") or ""
    ch_claim = signing.get("claim_id") or ""
    release = value.get("release_time") or claim.get("timestamp")
    upload_date = ""
    if release:
        try:
            upload_date = datetime.fromtimestamp(
                int(release), tz=timezone.utc
            ).strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            upload_date = ""
    duration = (value.get("video") or value.get("audio") or {}).get("duration")
    return VideoDetails(
        video_id=video_id,
        title=value.get("title") or claim.get("name") or "",
        channel_id=f"{ch_name}:{ch_claim}" if ch_name and ch_claim else "",
        channel_title=(signing.get("value") or {}).get("title") or ch_name,
        description=value.get("description") or "",
        view_count=None,
        like_count=None,
        duration=int(duration) if duration else None,
        upload_date=upload_date,
    )


@dataclass
class OdyseeStream:
    """A resolved Odysee stream (the fields StreamInfo conveys to clients)."""

    kind: Literal["hls", "progressive"]
    url: str
    title: str
    duration: int | None
    height: int | None = None
    view_count: int | None = None
    is_live: bool = False


_HLS_RES_RE = re.compile(r"RESOLUTION=(\d+)x(\d+)")
_HLS_CODECS_RE = re.compile(r'CODECS="([^"]*)"')
_HLS_AUDIO_PREFIXES = ("mp4a", "aac", "ac-3", "ec-3", "opus", "vorbis", "flac")


def _variant_has_audio(line: str) -> bool:
    """True when a variant's CODECS attribute carries an audio codec.

    A variant without one plays silent (audio-less ladder rungs exist in the
    wild); unknown CODECS count as muxed, as do legacy playlists without it.
    """
    m = _HLS_CODECS_RE.search(line)
    if not m:
        return True
    codecs = [c.strip().lower() for c in m.group(1).split(",")]
    return any(c.startswith(_HLS_AUDIO_PREFIXES) for c in codecs)


def _pick_hls_variant(
    master: str, master_url: str, max_height: int
) -> tuple[str | None, int | None]:
    """Best variant playlist of a master manifest honouring max_height.

    Same cap semantics as ytdlp._capped: best height that fits; when nothing
    fits, the lowest available (a cap must never make a video unplayable).
    A bare media playlist (no variants) is returned as-is. Returns (None, None)
    when no variant carries an audio track — the caller then falls back to the
    original file, which does (the old yt-dlp path made the same choice).
    """
    lines = [line.strip() for line in master.splitlines()]
    variants: list[tuple[int, bool, str]] = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        m = _HLS_RES_RE.search(line)
        height = int(m.group(2)) if m else 0
        for nxt in lines[i + 1 : i + 3]:
            if nxt and not nxt.startswith("#"):
                variants.append(
                    (height, _variant_has_audio(line), urllib.parse.urljoin(master_url, nxt))
                )
                break
    if not variants:
        return master_url, None
    with_audio = [v for v in variants if v[1]]
    if not with_audio:
        return None, None
    fitting = [v for v in with_audio if v[0] and v[0] <= max_height]
    if fitting:
        height, _, url = max(fitting, key=lambda v: v[0])
    else:
        # 0-height variants sort last, so a real resolution always wins.
        height, _, url = min(with_audio, key=lambda v: v[0] or 10**9)
    return url, height or None


async def stream(video_id: str, max_height: int = 1440) -> OdyseeStream | None:
    """Direct stream resolution through the SDK proxy (no yt-dlp).

    One `get` (~200 ms) yields the CDN streaming URL; a HEAD then reveals
    what that URL actually serves: the original file (progressive), or — for
    transcoded videos — a redirect to a master.m3u8 ladder from which the
    best variant under `max_height` is picked. Returns None when the proxy
    cannot provide a streaming URL (the caller falls back to yt-dlp); raises
    NotPlayableError for known non-media claims.
    """
    cached = _STREAM_CACHE.get(video_id)
    if cached is not None and time.monotonic() - cached[0] < _STREAM_CACHE_TTL:
        return cached[1]
    uri = _lbry_uri(video_id)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=_HEADERS) as client:
            got, resolved = await asyncio.gather(
                _proxy_call(client, "get", {"uri": uri}),
                _resolve_claim(client, video_id),
            )
    except Exception as exc:
        log.warning("odysee stream resolve failed for %s: %s", video_id, exc)
        return None
    streaming_url = got.get("streaming_url") if isinstance(got, dict) else None
    if not streaming_url:
        if resolved and not _is_playable(resolved.get("value") or {}):
            raise NotPlayableError(
                "not a playable video ("
                f"{(resolved.get('value') or {}).get('stream_type') or 'unknown type'})"
            )
        return None
    title = ""
    duration: int | None = None
    if resolved:
        value = resolved.get("value") or {}
        title = value.get("title") or ""
        raw = (value.get("video") or value.get("audio") or {}).get("duration")
        duration = int(raw) if raw else None
    kind = "progressive"
    url, height = streaming_url, None
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT, headers=_HEADERS, follow_redirects=True
        ) as client:
            head = await client.head(streaming_url, headers=_CDN_HEADERS)
            final_url = str(head.url)
            content_type = (head.headers.get("content-type") or "").lower()
            if final_url.endswith(".m3u8") or "mpegurl" in content_type:
                manifest = await client.get(final_url, headers=_CDN_HEADERS)
                manifest.raise_for_status()
                picked, height = _pick_hls_variant(manifest.text, final_url, max_height)
                if picked is not None:
                    url, kind = picked, "hls"
                # picked None: no variant carries audio — keep the original
                # file (progressive), which does.
    except Exception as exc:
        log.warning("odysee manifest probe failed for %s: %s", video_id, exc)
        # The direct URL still plays as progressive.
    out = OdyseeStream(
        kind=kind,
        url=url,
        title=title,
        duration=duration,
        height=height,
        view_count=None,
    )
    if len(_STREAM_CACHE) >= _STREAM_CACHE_MAX and video_id not in _STREAM_CACHE:
        oldest = min(_STREAM_CACHE, key=lambda k: _STREAM_CACHE[k][0])
        del _STREAM_CACHE[oldest]
    _STREAM_CACHE[video_id] = (time.monotonic(), out)
    return out


async def stream_type(video_id: str) -> str:
    """Stream type of a claim ('video', 'audio', 'image', 'document', …).

    Best effort: returns "" when the claim is unknown or the lookup fails. Used
    to explain playback failures on non-media claims, so it never raises.
    """
    claim_id = claim_id_from_video_id(video_id)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            result = await _proxy_call(
                client,
                "claim_search",
                {"claim_ids": [claim_id], "page": 1, "page_size": 1, "no_totals": True},
            )
        items = result.get("items") or []
        return ((items[0].get("value") or {}) if items else {}).get("stream_type") or ""
    except Exception as exc:
        log.debug("odysee stream_type lookup failed for %s: %s", claim_id, exc)
        return ""


async def comments(
    claim_id: str, page: int = 1, page_size: int = 50, parent_id: str | None = None
) -> CommentsResponse:
    """List comments for a claim (public Commentron API) with reaction counts."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{COMMENTS_URL}?m=comment.List",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "comment.List",
                    "params": {
                        "claim_id": claim_id,
                        "page": page,
                        "page_size": page_size,
                        "sort_by": 3,
                        **({"parent_id": parent_id} if parent_id else {"top_level": True}),
                    },
                },
                headers=_HEADERS,
            )
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                error = body["error"]
                message = error.get("message", "") if isinstance(error, dict) else str(error)
                if "disabled" in message.lower():
                    # comments disabled by the creator: a normal state, not a failure
                    return CommentsResponse(items=[], total=0, disabled=True)
                raise OdyseeError(f"comment.List: {body['error']}")
            result = body.get("result") or {}
            items = result.get("items") or []
            reactions: dict[str, dict] = {}
            comment_ids = [c["comment_id"] for c in items if c.get("comment_id")]
            if comment_ids:
                try:
                    resp = await client.post(
                        f"{COMMENTS_URL}?m=reaction.List",
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "reaction.List",
                            "params": {"comment_ids": ",".join(comment_ids)},
                        },
                        headers=_HEADERS,
                    )
                    resp.raise_for_status()
                    reactions = (resp.json().get("result") or {}).get(
                        "others_reactions"
                    ) or {}
                except Exception as exc:
                    log.warning("odysee reaction.List failed: %s", exc)
                    reactions = {}  # reactions are cosmetic; ignore failures
    except OdyseeError:
        raise
    except Exception as exc:
        log.warning("odysee comments fetch failed for %s: %s", claim_id, exc)
        raise OdyseeError(f"comments fetch failed: {exc}") from exc
    out: list[CommentOut] = []
    for c in items:
        comment_id = c.get("comment_id") or ""
        if not comment_id:
            continue
        reaction = reactions.get(comment_id) or {}
        out.append(
            CommentOut(
                comment_id=comment_id,
                text=c.get("comment") or "",
                channel_name=c.get("channel_name") or "",
                timestamp=c.get("timestamp"),
                replies=c.get("replies") or 0,
                likes=reaction.get("like") or 0,
                dislikes=reaction.get("dislike") or 0,
                is_pinned=bool(c.get("is_pinned")),
            )
        )
    return CommentsResponse(items=out, total=result.get("total_items") or len(out))
