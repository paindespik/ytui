"""Video details, stream resolution, YouTube playlist/channel listings,
and like/comment actions."""

from __future__ import annotations

from typing import Literal

import anyio
from fastapi import APIRouter, HTTPException, Query, Request

from ..models import (
    Channel,
    ChannelVideosResponse,
    CommentIn,
    PlaylistVideosResponse,
    StreamInfo,
    Video,
    VideoDetails,
)
from ..services import ytdlp
from ..services.youtube import ApiError, AuthError

router = APIRouter()

Platform = Literal["youtube", "bitchute"]


def _video_url(video_id: str, platform: Platform) -> str:
    return Video(video_id=video_id, title="", platform=platform).url


@router.get("/channels/{channel_id}/videos", response_model=ChannelVideosResponse)
async def channel_videos(
    channel_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    platform: Platform = "youtube",
) -> ChannelVideosResponse:
    url = Video(video_id=channel_id, title="", kind="channel", platform=platform).url
    try:
        items = await ytdlp.channel_videos(url, limit=limit)
    except ytdlp.UpstreamError as exc:
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
    url = Video(video_id=playlist_id, title="", kind="playlist", platform=platform).url
    try:
        items, title = await ytdlp.playlist_videos(url, limit=limit)
    except ytdlp.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=f"Playlist listing failed: {exc}") from exc
    return PlaylistVideosResponse(items=items, title=title)


@router.get("/videos/{video_id}", response_model=VideoDetails)
async def video_details(video_id: str, platform: Platform = "youtube") -> VideoDetails:
    try:
        details = await ytdlp.video_details(_video_url(video_id, platform))
    except ytdlp.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=f"Details fetch failed: {exc}") from exc
    if not details.video_id:
        details.video_id = video_id
    return details


@router.get("/videos/{video_id}/streams", response_model=StreamInfo)
async def video_streams(
    video_id: str,
    platform: Platform = "youtube",
    max_height: int = Query(default=1080, ge=144, le=4320),
    audio_only: bool = False,
) -> StreamInfo:
    try:
        return await ytdlp.resolve_streams(
            _video_url(video_id, platform), max_height=max_height, audio_only=audio_only
        )
    except ytdlp.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=f"Stream resolution failed: {exc}") from exc


@router.post("/videos/{video_id}/like", status_code=204)
async def like_video(video_id: str, request: Request) -> None:
    yt = request.app.state.youtube_service
    try:
        await anyio.to_thread.run_sync(yt.like_video, video_id)
    except AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/videos/{video_id}/comment", status_code=204)
async def comment_video(video_id: str, body: CommentIn, request: Request) -> None:
    yt = request.app.state.youtube_service
    try:
        await anyio.to_thread.run_sync(yt.post_comment, video_id, body.text)
    except AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
