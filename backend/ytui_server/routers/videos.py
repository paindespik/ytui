"""Video details, stream resolution, YouTube playlist/channel listings,
and like/comment actions."""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from typing import Literal

import anyio
import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..models import (
    Channel,
    ChannelVideosResponse,
    CommentIn,
    CommentsResponse,
    PlaylistVideosResponse,
    SearchResponse,
    SponsorSegmentsOut,
    StreamInfo,
    Video,
    VideoDetails,
)
from ..services import dash, odysee, tiktok, twitch, ytdlp
from ..services.youtube import ApiError, AuthError

router = APIRouter()

log = logging.getLogger(__name__)

Platform = Literal["youtube", "bitchute", "odysee", "twitch", "tiktok"]

_ODYSEE_WRITE_DETAIL = "Odysee likes/comments require a LBRY wallet signature (not supported)"


def _write_unsupported_detail(platform: Platform) -> str:
    if platform == "odysee":
        return _ODYSEE_WRITE_DETAIL
    return f"Likes/comments are not supported for {platform}"


def _video_url(video_id: str, platform: Platform) -> str:
    return Video(video_id=video_id, title="", platform=platform).url


@router.get("/channels/{channel_id}/videos", response_model=ChannelVideosResponse)
async def channel_videos(
    channel_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    platform: Platform = "youtube",
) -> ChannelVideosResponse:
    try:
        if platform == "odysee":
            items = await odysee.channel_videos(channel_id, limit=limit)
        else:
            url = Video(video_id=channel_id, title="", kind="channel", platform=platform).url
            items = await ytdlp.channel_videos(url, limit=limit)
    except (ytdlp.UpstreamError, odysee.OdyseeError) as exc:
        raise HTTPException(status_code=502, detail=f"Channel listing failed: {exc}") from exc
    title = request.app.state.db.get_channel_name(channel_id) or ""
    if not title and items:
        title = items[0].channel_title
    return ChannelVideosResponse(
        items=items, channel=Channel(channel_id=channel_id, title=title, platform=platform)
    )


@router.get("/ytplaylists/{playlist_id}/videos", response_model=PlaylistVideosResponse)
async def playlist_videos(
    playlist_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    platform: Platform = "youtube",
) -> PlaylistVideosResponse:
    if platform == "odysee":
        # odysee.com/$/playlist/{id} is a JS route with no yt-dlp extractor
        raise HTTPException(status_code=400, detail="Odysee playlists are not supported")
    url = Video(video_id=playlist_id, title="", kind="playlist", platform=platform).url
    try:
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


@router.get("/videos/{video_id}", response_model=VideoDetails)
async def video_details(video_id: str, platform: Platform = "youtube") -> VideoDetails:
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
    max_height: int = Query(default=1080, ge=144, le=4320),
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
                playlist = await twitch.resolve_live_playlist(client, login, proxies)
            if playlist is not None:
                entry = request.app.state.live_monitor.lives.get(login)
                return StreamInfo(
                    kind="hls", url=playlist, title=entry[0].title if entry else ""
                )
    if platform == "tiktok" and ":" in video_id:
        # Live id "username:room_id": direct webcast resolution (yt-dlp's
        # TikTokLiveIE wrongly fails on FLV-only rooms); yt-dlp fallback below.
        username = video_id.split(":", 1)[0]
        try:
            async with httpx.AsyncClient(timeout=tiktok.TIMEOUT) as client:
                resolved = await tiktok.resolve_live_stream(client, username)
        except (httpx.HTTPError, tiktok.TikTokError) as exc:
            log.debug("tiktok live resolve failed for %s: %s", username, exc)
            resolved = None
        if resolved is not None:
            url, title = resolved
            return StreamInfo(kind="hls", url=url, title=title)
    try:
        return await ytdlp.resolve_streams(
            _video_url(video_id, platform),
            max_height=max_height,
            audio_only=audio_only,
            sub_langs=sub_langs,
        )
    except ytdlp.UpstreamError as exc:
        await _reject_unplayable_odysee(video_id, platform)
        raise HTTPException(status_code=502, detail=f"Stream resolution failed: {exc}") from exc


@router.get("/videos/{video_id}/mpd")
async def video_mpd(
    video_id: str,
    request: Request,
    platform: Platform = "youtube",
    max_height: int = Query(default=1080, ge=144, le=4320),
) -> Response:
    try:
        split = await ytdlp.resolve_split_mp4(_video_url(video_id, platform), max_height)
    except ytdlp.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=f"Stream resolution failed: {exc}") from exc
    if split is None:
        raise HTTPException(status_code=404, detail="No split MP4 formats")
    client = request.app.state.proxy_client
    video_ranges, audio_ranges = await asyncio.gather(
        dash.probe_ranges(client, split.video_url),
        dash.probe_ranges(client, split.audio_url),
    )
    if video_ranges is None or audio_ranges is None:
        raise HTTPException(status_code=404, detail="sidx not found")
    proxy = lambda u: "/api/proxy?url=" + urllib.parse.quote(u, safe="")  # noqa: E731
    mpd_xml = dash.build_mpd(split, proxy, video_ranges, audio_ranges)
    return Response(
        content=mpd_xml,
        media_type="application/dash+xml",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/videos/{video_id}/related", response_model=SearchResponse)
async def related_videos(
    video_id: str,
    platform: Platform = "youtube",
    limit: int = Query(default=20, ge=1, le=40),
) -> SearchResponse:
    if platform != "youtube":
        return SearchResponse(items=[])
    try:
        items = await ytdlp.related_videos(video_id, limit=limit)
    except ytdlp.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=f"Related fetch failed: {exc}") from exc
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
    platform: Platform = "youtube",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> CommentsResponse:
    if platform != "odysee":
        raise HTTPException(
            status_code=501, detail="Comment listing is only available for Odysee"
        )
    try:
        return await odysee.comments(
            odysee.claim_id_from_video_id(video_id), page=page, page_size=page_size
        )
    except odysee.OdyseeError as exc:
        raise HTTPException(status_code=502, detail=f"Comments fetch failed: {exc}") from exc


@router.post("/videos/{video_id}/like", status_code=204)
async def like_video(video_id: str, request: Request, platform: Platform = "youtube") -> None:
    if platform != "youtube":
        raise HTTPException(status_code=409, detail=_write_unsupported_detail(platform))
    yt = request.app.state.youtube_service
    try:
        await anyio.to_thread.run_sync(yt.like_video, video_id)
    except AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/videos/{video_id}/comment", status_code=204)
async def comment_video(
    video_id: str, body: CommentIn, request: Request, platform: Platform = "youtube"
) -> None:
    if platform != "youtube":
        raise HTTPException(status_code=409, detail=_write_unsupported_detail(platform))
    yt = request.app.state.youtube_service
    try:
        await anyio.to_thread.run_sync(yt.post_comment, video_id, body.text)
    except AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
