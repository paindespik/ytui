"""Video details, stream resolution, YouTube playlist/channel listings,
and like/comment actions."""

from __future__ import annotations

import asyncio
import functools
import logging
import urllib.parse
from collections.abc import Callable
from typing import Literal

import anyio
import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..models import (
    Channel,
    ChannelVideosResponse,
    CommentIn,
    CommentOut,
    CommentsResponse,
    PlaylistVideosResponse,
    RatingOut,
    SearchResponse,
    SponsorSegmentsOut,
    StreamInfo,
    Video,
    VideoDetails,
)
from ..services import bitchute, crowdbunker, dash, odysee, tiktok, twitch, ytdlp
from ..services.live import live_key
from ..services.youtube import ApiError, AuthError

router = APIRouter()

log = logging.getLogger(__name__)

Platform = Literal["youtube", "bitchute", "odysee", "twitch", "tiktok", "crowdbunker"]

# Channel listing order: newest first, or most viewed first.
ChannelSort = Literal["date", "views"]
# Platforms whose channel listing can be ordered by views.
_VIEWS_SORT_PLATFORMS = ("youtube", "bitchute", "crowdbunker")

_ODYSEE_WRITE_DETAIL = "Odysee likes/comments require a LBRY wallet signature (not supported)"


def _write_unsupported_detail(platform: Platform) -> str:
    if platform == "odysee":
        return _ODYSEE_WRITE_DETAIL
    return f"Likes/comments are not supported for {platform}"


def _video_url(video_id: str, platform: Platform) -> str:
    return Video(video_id=video_id, title="", platform=platform).url


# No platform but YouTube and Odysee exposes an in-channel search upstream; for
# the others the newest uploads are listed once and filtered on title locally.
# Odysee shares the local fallback for short queries: Lighthouse 400s on terms
# under 3 characters.
_CHANNEL_SCAN_CAP = 200
_MIN_ODYSEE_SEARCH_LEN = 3


def _channel_url(channel_id: str, platform: Platform) -> str:
    return Video(video_id=channel_id, title="", kind="channel", platform=platform).url


async def _search_channel(
    channel_id: str, platform: Platform, query: str, limit: int, offset: int
) -> list[Video]:
    if platform == "youtube":
        url = _channel_url(channel_id, platform)
        return await ytdlp.channel_search(url, query, limit=limit, offset=offset)
    if platform == "odysee" and len(query.strip()) >= _MIN_ODYSEE_SEARCH_LEN:
        return await odysee.search(query, limit=limit, offset=offset, channel_id=channel_id)
    if platform == "crowdbunker" and len(query.strip()) >= crowdbunker.MIN_SEARCH_LEN:
        return await crowdbunker.channel_search(channel_id, query, limit=limit, offset=offset)
    # Local title scan: short Odysee/CrowdBunker queries (their search APIs
    # 400 on them) and every platform without an upstream in-channel search.
    if platform == "odysee":
        items = await odysee.channel_videos(channel_id, limit=_CHANNEL_SCAN_CAP)
    elif platform == "crowdbunker":
        items = await crowdbunker.channel_videos(channel_id, limit=_CHANNEL_SCAN_CAP)
    elif platform == "bitchute":
        items = await bitchute.channel_videos(channel_id, limit=_CHANNEL_SCAN_CAP)
    else:
        items = await ytdlp.channel_videos(
            _channel_url(channel_id, platform), limit=_CHANNEL_SCAN_CAP
        )
    needle = query.casefold()
    matches = [item for item in items if needle in item.title.casefold()]
    return matches[offset : offset + limit]


async def _channel_videos_by_views(
    channel_id: str, platform: Platform, limit: int, offset: int
) -> list[Video]:
    if platform == "bitchute":
        return await bitchute.channel_videos(channel_id, limit=limit, offset=offset, sort="views")
    if platform == "crowdbunker":
        return await crowdbunker.popular_videos(channel_id, limit=limit, offset=offset)
    return await ytdlp.channel_popular_videos(channel_id, limit=limit, offset=offset)


@router.get("/channels/{channel_id}/videos", response_model=ChannelVideosResponse)
async def channel_videos(
    channel_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    platform: Platform = "youtube",
    q: str | None = Query(default=None, min_length=1),
    sort: ChannelSort = "date",
) -> ChannelVideosResponse:
    """One page of a channel's videos, newest first (`sort=date`) or most
    viewed first (`sort=views`: YouTube, BitChute, CrowdBunker).

    A search (`q`) keeps the order of the platform's search and ignores
    `sort`.
    """
    by_views = sort == "views" and not q
    if by_views and platform not in _VIEWS_SORT_PLATFORMS:
        raise HTTPException(
            status_code=501, detail=f"Sorting by views is not supported for {platform}"
        )
    if platform == "tiktok":
        # TikTok channel video pages are not scrapable without auth; channels
        # surface through the lives endpoint only, so answer empty right away.
        return ChannelVideosResponse(
            items=[],
            channel=Channel(
                channel_id=channel_id,
                title=request.app.state.db.get_channel_name(channel_id) or "",
                platform=platform,
            ),
            has_more=False,
        )
    # One extra item is fetched to tell "more available" from "exhausted".
    probe = limit + 1
    try:
        if q:
            items = await _search_channel(channel_id, platform, q, probe, offset)
        elif by_views:
            items = await _channel_videos_by_views(channel_id, platform, probe, offset)
        elif platform == "odysee":
            items = await odysee.channel_videos(channel_id, limit=probe, offset=offset)
        elif platform == "crowdbunker":
            items = await crowdbunker.channel_videos(channel_id, limit=probe, offset=offset)
        elif platform == "bitchute":
            items = await bitchute.channel_videos(channel_id, limit=probe, offset=offset)
        else:
            items = await ytdlp.channel_videos(
                _channel_url(channel_id, platform), limit=probe, offset=offset
            )
    except (
        ytdlp.UpstreamError,
        odysee.OdyseeError,
        crowdbunker.CrowdBunkerError,
        bitchute.BitChuteError,
    ) as exc:
        raise HTTPException(status_code=502, detail=f"Channel listing failed: {exc}") from exc
    has_more = len(items) > limit
    items = items[:limit]
    title = request.app.state.db.get_channel_name(channel_id) or ""
    if not title and items:
        title = items[0].channel_title
    return ChannelVideosResponse(
        items=items,
        channel=Channel(channel_id=channel_id, title=title, platform=platform),
        has_more=has_more,
    )


# Platforms whose channels expose their playlists: YouTube (the channel's
# Playlists tab) and BitChute (the owner profile's public playlists).
_CHANNEL_PLAYLIST_PLATFORMS = ("youtube", "bitchute")


@router.get("/channels/{channel_id}/playlists", response_model=ChannelVideosResponse)
async def channel_playlists(
    channel_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    platform: Platform = "youtube",
) -> ChannelVideosResponse:
    """One page of a channel's playlists (items of kind "playlist")."""
    if platform not in _CHANNEL_PLAYLIST_PLATFORMS:
        raise HTTPException(
            status_code=501, detail=f"Channel playlists are not supported for {platform}"
        )
    # One extra item is fetched to tell "more available" from "exhausted".
    probe = limit + 1
    try:
        if platform == "bitchute":
            items = await bitchute.channel_playlists(channel_id, limit=probe, offset=offset)
        else:
            items = await ytdlp.channel_playlists(
                _channel_url(channel_id, platform), limit=probe, offset=offset
            )
    except bitchute.BitChuteNotFound as exc:
        raise HTTPException(status_code=404, detail="Channel not found") from exc
    except (ytdlp.UpstreamError, bitchute.BitChuteError) as exc:
        raise HTTPException(
            status_code=502, detail=f"Channel playlists listing failed: {exc}"
        ) from exc
    has_more = len(items) > limit
    items = items[:limit]
    title = request.app.state.db.get_channel_name(channel_id) or ""
    if not title and items:
        title = items[0].channel_title
    return ChannelVideosResponse(
        items=items,
        channel=Channel(channel_id=channel_id, title=title, platform=platform),
        has_more=has_more,
    )


@router.get("/ytplaylists/{playlist_id}/videos", response_model=PlaylistVideosResponse)
async def playlist_videos(
    playlist_id: str,
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
    platform: Platform = "youtube",
) -> PlaylistVideosResponse:
    if platform == "odysee":
        # odysee.com/$/playlist/{id} is a JS route with no yt-dlp extractor
        raise HTTPException(status_code=400, detail="Odysee playlists are not supported")
    if platform == "twitch":
        # Twitch collections have no stable public URL form nor yt-dlp support
        raise HTTPException(status_code=400, detail="Twitch playlists are not supported")
    try:
        if platform == "youtube":
            # Personal mixes and liked music need the signed-in session.
            items, title = await request.app.state.music_service.youtube_playlist(
                playlist_id, limit=limit
            )
        elif platform == "bitchute":
            # The API carries thumbnails and dates; yt-dlp is its fallback.
            items, title = await bitchute.playlist_videos(playlist_id, limit=limit)
        else:
            url = Video(video_id=playlist_id, title="", kind="playlist", platform=platform).url
            items, title = await ytdlp.playlist_videos(url, limit=limit)
    except ytdlp.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=f"Playlist listing failed: {exc}") from exc
    return PlaylistVideosResponse(items=items, title=title)


async def _reject_unplayable_odysee(video_id: str, platform: Platform) -> None:
    """Raise 415 when an Odysee failure is really a non-media claim.

    Odysee channels publish images, PDFs and text posts; yt-dlp's LBRY extractor
    reports a bogus "This stream is not live" for them. Listings filter those out,
    but stale history/playlist entries still reach here. Error path only: one extra
    lookup, never on the happy path.
    """
    if platform != "odysee":
        return
    kind = await odysee.stream_type(video_id)
    if kind and kind not in odysee.PLAYABLE_STREAM_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"This Odysee post is {kind} content, not a playable video",
        )


async def _crowdbunker_details(video_id: str) -> VideoDetails:
    """CrowdBunker details from the API: yt-dlp's carry no channel (it reads
    the post's `channel`, now empty), which broke "go to channel" and the
    related listing. yt-dlp stays the fallback."""
    try:
        return await crowdbunker.video_details(video_id)
    except crowdbunker.CrowdBunkerError as exc:
        log.warning("crowdbunker direct details failed for %s: %s", video_id, exc)
    try:
        return await ytdlp.video_details(_video_url(video_id, "crowdbunker"))
    except ytdlp.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=f"Details fetch failed: {exc}") from exc


@router.get("/videos/{video_id}", response_model=VideoDetails)
async def video_details(video_id: str, platform: Platform = "youtube") -> VideoDetails:
    if platform == "crowdbunker":
        return await _crowdbunker_details(video_id)
    if platform == "odysee":
        # The SDK proxy answers in ~200 ms where yt-dlp's api.lbry.tv takes
        # 10-30 s; fall back to yt-dlp only when the proxy cannot serve it.
        try:
            return await odysee.video_details(video_id)
        except odysee.NotPlayableError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except Exception as exc:
            log.warning("odysee direct details failed for %s: %s", video_id, exc)
    try:
        details = await ytdlp.video_details(_video_url(video_id, platform))
    except ytdlp.UpstreamError as exc:
        await _reject_unplayable_odysee(video_id, platform)
        raise HTTPException(status_code=502, detail=f"Details fetch failed: {exc}") from exc
    if not details.video_id or platform == "odysee":
        # yt-dlp reports the bare claim_id for Odysee; keep the requested 'name:claim' id
        details.video_id = video_id
    return details


@router.get("/videos/{video_id}/streams", response_model=StreamInfo)
async def video_streams(
    video_id: str,
    request: Request,
    platform: Platform = "youtube",
    max_height: int = Query(default=1440, ge=144, le=4320),
    audio_only: bool = False,
    sub_langs: str = "",
) -> StreamInfo:
    if platform == "twitch" and ":" in video_id:
        # Live id "login:stream_id": try the ad-free playlist proxies first,
        # then degrade to the direct (ad-fed) stream via yt-dlp below.
        login = video_id.split(":", 1)[0]
        proxies = [
            p.strip()
            for p in request.app.state.settings.twitch_proxies.split(",")
            if p.strip()
        ]
        if proxies:
            async with httpx.AsyncClient(timeout=twitch.TIMEOUT) as client:
                resolved = await twitch.resolve_live_playlist(
                    client, login, proxies, max_height
                )
            if resolved is not None:
                playlist, height = resolved
                entry = request.app.state.live_monitor.lives.get(live_key("twitch", login))
                return StreamInfo(
                    kind="hls",
                    url=playlist,
                    title=entry[0].title if entry else "",
                    height=height,
                )
    if platform == "tiktok" and ":" in video_id:
        # Live id "username:room_id": direct webcast resolution (yt-dlp's
        # TikTokLiveIE wrongly fails on FLV-only rooms); yt-dlp fallback below.
        username = video_id.split(":", 1)[0]
        try:
            async with httpx.AsyncClient(timeout=tiktok.TIMEOUT) as client:
                resolved = await tiktok.resolve_live_stream(client, username, max_height)
        except (httpx.HTTPError, tiktok.TikTokError) as exc:
            log.debug("tiktok live resolve failed for %s: %s", username, exc)
            resolved = None
        if resolved is not None:
            url, title, height = resolved
            return StreamInfo(kind="hls", url=url, title=title, height=height)
    if platform == "odysee":
        # Direct proxy resolution (~200 ms, cached); yt-dlp fallback below when
        # the proxy has no streaming URL for this claim.
        try:
            direct = await odysee.stream(video_id, max_height=max_height)
        except odysee.NotPlayableError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        if direct is not None:
            return StreamInfo(
                kind=direct.kind,
                url=direct.url,
                title=direct.title,
                duration=direct.duration,
                height=direct.height,
            )
    try:
        info = await ytdlp.resolve_streams(
            _video_url(video_id, platform),
            max_height=max_height,
            audio_only=audio_only,
            sub_langs=sub_langs,
        )
        if platform == "youtube" and info.is_live and info.kind == "hls":
            # Direct segment URLs die ~26 s in on this server's bucket: point
            # capable clients at the self-healing proxy (see routers/proxy.py).
            info.proxy_path = (
                f"/api/live/{video_id}/playlist.m3u8?max_height={max_height}"
            )
        return info
    except ytdlp.UpstreamError as exc:
        await _reject_unplayable_odysee(video_id, platform)
        raise HTTPException(status_code=502, detail=f"Stream resolution failed: {exc}") from exc


@router.get("/videos/{video_id}/mpd")
async def video_mpd(
    video_id: str,
    request: Request,
    platform: Platform = "youtube",
    max_height: int = Query(default=1440, ge=144, le=4320),
) -> Response:
    proxy = lambda u: "/api/proxy?url=" + urllib.parse.quote(u, safe="")  # noqa: E731
    mpd_xml = await render_mpd(
        _video_url(video_id, platform), max_height, request.app.state.proxy_client, proxy
    )
    return Response(
        content=mpd_xml,
        media_type="application/dash+xml",
        headers={"Cache-Control": "no-store"},
    )


async def render_mpd(
    url: str, max_height: int, client: httpx.AsyncClient, proxy: Callable[[str], str]
) -> str:
    """DASH manifest of a video's split MP4 pair, media URLs mapped by ``proxy``.

    Raises 404 when the video has no usable split pair (callers fall back to
    progressive) and 502 when the resolution itself fails.
    """
    try:
        split = await ytdlp.resolve_split_mp4(url, max_height)
    except ytdlp.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=f"Stream resolution failed: {exc}") from exc
    if split is None:
        raise HTTPException(status_code=404, detail="No split MP4 formats")
    video_ranges, audio_ranges = await asyncio.gather(
        dash.probe_ranges(client, split.video_url),
        dash.probe_ranges(client, split.audio_url),
    )
    if video_ranges is None or audio_ranges is None:
        raise HTTPException(status_code=404, detail="sidx not found")
    return dash.build_mpd(split, proxy, video_ranges, audio_ranges)


@router.get("/videos/{video_id}/related", response_model=SearchResponse)
async def related_videos(
    video_id: str,
    platform: Platform = "youtube",
    limit: int = Query(default=20, ge=1, le=40),
) -> SearchResponse:
    if platform == "youtube":
        try:
            items = await ytdlp.related_videos(video_id, limit=limit)
        except ytdlp.UpstreamError as exc:
            raise HTTPException(
                status_code=502, detail=f"Related fetch failed: {exc}"
            ) from exc
        return SearchResponse(items=items)
    if platform == "tiktok":
        return SearchResponse(items=[])
    # No upstream "up next" API: fall back to the newest uploads from the same
    # channel. The channel id comes from the (TTL-cached) video details, and the
    # current video is excluded from the listing.
    try:
        if platform == "crowdbunker":
            details = await _crowdbunker_details(video_id)
        else:
            details = await ytdlp.video_details(_video_url(video_id, platform))
    except HTTPException as exc:
        raise HTTPException(
            status_code=502, detail=f"Related fetch failed: {exc.detail}"
        ) from exc
    except ytdlp.UpstreamError as exc:
        raise HTTPException(
            status_code=502, detail=f"Related fetch failed: {exc}"
        ) from exc
    channel_id = details.channel_id
    if not channel_id:
        return SearchResponse(items=[])
    try:
        if platform == "odysee":
            items = await odysee.channel_videos(channel_id, limit=limit)
        elif platform == "crowdbunker":
            items = await crowdbunker.channel_videos(channel_id, limit=limit)
        elif platform == "bitchute":
            items = await bitchute.channel_videos(channel_id, limit=limit)
        else:
            items = await ytdlp.channel_videos(
                _channel_url(channel_id, platform), limit=limit
            )
    except (
        ytdlp.UpstreamError,
        odysee.OdyseeError,
        crowdbunker.CrowdBunkerError,
    ) as exc:
        raise HTTPException(status_code=502, detail=f"Related fetch failed: {exc}") from exc
    items = [item for item in items if item.video_id != video_id][:limit]
    return SearchResponse(items=items)


@router.get("/videos/{video_id}/sponsor", response_model=SponsorSegmentsOut)
async def sponsor_segments(
    video_id: str, request: Request, platform: Platform = "youtube"
) -> SponsorSegmentsOut:
    if platform != "youtube":
        return SponsorSegmentsOut()
    return SponsorSegmentsOut(
        segments=await request.app.state.sponsorblock.get(video_id)
    )


@router.get("/videos/{video_id}/comments", response_model=CommentsResponse)
async def video_comments(
    video_id: str,
    request: Request,
    platform: Platform = "youtube",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
) -> CommentsResponse:
    """Top-level comments. Pass back `next_cursor` as `cursor` for the next page."""
    if platform == "youtube":
        yt = request.app.state.youtube_service
        try:
            return await anyio.to_thread.run_sync(
                functools.partial(yt.list_comments, video_id, cursor=cursor, limit=page_size)
            )
        except AuthError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    if platform != "odysee":
        raise HTTPException(
            status_code=501, detail=f"Comment listing is not available for {platform}"
        )
    # Commentron paginates by number; `cursor` carries the next page for clients
    # that only speak cursors.
    if cursor and cursor.isdigit():
        page = int(cursor)
    try:
        response = await odysee.comments(
            odysee.claim_id_from_video_id(video_id), page=page, page_size=page_size
        )
    except odysee.OdyseeError as exc:
        raise HTTPException(status_code=502, detail=f"Comments fetch failed: {exc}") from exc
    if len(response.items) == page_size:
        response.next_cursor = str(page + 1)
    return response


@router.get(
    "/videos/{video_id}/comments/{comment_id}/replies", response_model=CommentsResponse
)
async def comment_replies(
    video_id: str,
    comment_id: str,
    request: Request,
    platform: Platform = "youtube",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
) -> CommentsResponse:
    """Replies to one comment. `video_id` is only needed by Odysee (claim id)."""
    if platform == "youtube":
        yt = request.app.state.youtube_service
        try:
            return await anyio.to_thread.run_sync(
                functools.partial(
                    yt.list_replies, comment_id, cursor=cursor, limit=page_size
                )
            )
        except AuthError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    if platform != "odysee":
        raise HTTPException(
            status_code=501, detail=f"Comment replies are not available for {platform}"
        )
    if cursor and cursor.isdigit():
        page = int(cursor)
    try:
        response = await odysee.comments(
            odysee.claim_id_from_video_id(video_id),
            page=page,
            page_size=page_size,
            parent_id=comment_id,
        )
    except odysee.OdyseeError as exc:
        raise HTTPException(status_code=502, detail=f"Replies fetch failed: {exc}") from exc
    if len(response.items) == page_size:
        response.next_cursor = str(page + 1)
    return response


@router.post("/videos/{video_id}/comments/{comment_id}/reply", response_model=CommentOut)
async def reply_to_comment(
    video_id: str,
    comment_id: str,
    body: CommentIn,
    request: Request,
    platform: Platform = "youtube",
) -> CommentOut:
    """Post a reply and return it, so clients can append it right away."""
    if platform != "youtube":
        raise HTTPException(status_code=409, detail=_write_unsupported_detail(platform))
    yt = request.app.state.youtube_service
    try:
        return await anyio.to_thread.run_sync(yt.post_reply, comment_id, body.text)
    except AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/videos/{video_id}/rating", response_model=RatingOut)
async def video_rating(video_id: str, request: Request, platform: Platform = "youtube") -> RatingOut:
    """The signed-in account's rating, so a client can show the like button state."""
    if platform != "youtube":
        raise HTTPException(status_code=409, detail=_write_unsupported_detail(platform))
    yt = request.app.state.youtube_service
    try:
        return RatingOut(rating=await anyio.to_thread.run_sync(yt.get_rating, video_id))
    except AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/videos/{video_id}/like", status_code=204)
async def like_video(
    video_id: str,
    request: Request,
    platform: Platform = "youtube",
    rating: Literal["like", "none"] = "like",
) -> None:
    """Like a video, or drop an existing like with `rating=none`."""
    if platform != "youtube":
        raise HTTPException(status_code=409, detail=_write_unsupported_detail(platform))
    yt = request.app.state.youtube_service
    try:
        await anyio.to_thread.run_sync(yt.rate_video, video_id, rating)
    except AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/videos/{video_id}/comment", response_model=CommentOut)
async def comment_video(
    video_id: str, body: CommentIn, request: Request, platform: Platform = "youtube"
) -> CommentOut:
    """Post a top-level comment and return it, so clients can show it right away."""
    if platform != "youtube":
        raise HTTPException(status_code=409, detail=_write_unsupported_detail(platform))
    yt = request.app.state.youtube_service
    try:
        return await anyio.to_thread.run_sync(yt.post_comment, video_id, body.text)
    except AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
