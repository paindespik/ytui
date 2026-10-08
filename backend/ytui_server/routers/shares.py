"""Share links management (authenticated): create or extend the public link
of one video, list them, revoke one. The public side lives in share_public.py."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from ..db import ShareRow
from ..models import ShareIn, ShareOut, Video
from ..services import share

router = APIRouter()


def share_video(row: ShareRow) -> Video:
    return Video.model_validate(
        {
            "video_id": row.video_id,
            "title": row.title,
            "channel_title": row.channel_title,
            "thumbnail_url": row.thumbnail_url,
            "platform": row.platform,
        }
    )


def share_out(row: ShareRow) -> ShareOut:
    return ShareOut(
        token=row.token,
        path=f"/s/{row.token}",
        video=share_video(row),
        created_at=datetime.fromtimestamp(row.created_at, tz=timezone.utc),
        expires_at=(
            datetime.fromtimestamp(row.expires_at, tz=timezone.utc)
            if row.expires_at is not None
            else None
        ),
    )


@router.post("/shares", response_model=ShareOut)
async def create_share(body: ShareIn, request: Request) -> ShareOut:
    """The public link of a video: created, or extended when it already exists.

    A Twitch/TikTok live is pinned to the stream on air right now: its id is
    resolved by channel, and the link must not play the channel's next live.
    """
    video = body.video
    if video.kind != "video":
        raise HTTPException(status_code=400, detail="Only videos can be shared")
    if share.is_channel_live(video.platform, video.video_id):
        login = video.video_id.split(":", 1)[0]
        try:
            live = await share.current_live(video.platform, login)
        except share.LiveCheckError as exc:
            raise HTTPException(status_code=502, detail=f"Live check failed: {exc}") from exc
        if live is None:
            raise HTTPException(status_code=409, detail="This channel is no longer live")
        video = video.model_copy(
            update={
                "video_id": live.video_id,
                "title": video.title or live.title,
                "channel_title": video.channel_title or live.channel_title,
                "thumbnail_url": video.thumbnail_url or live.thumbnail_url,
            }
        )
    ttl = request.app.state.settings.share_ttl_days * 24 * 3600
    return share_out(request.app.state.db.share_video(video, ttl))


@router.get("/shares", response_model=list[ShareOut])
async def list_shares(request: Request) -> list[ShareOut]:
    return [
        share_out(row)
        for row in request.app.state.db.list_shares()
        if not row.expired()
    ]


@router.delete("/shares/{token}", status_code=204)
async def revoke_share(token: str, request: Request) -> None:
    """Revoke a link: its page and every media URL it handed out die at once."""
    if not request.app.state.db.delete_share(token):
        raise HTTPException(status_code=404, detail="Unknown share link")
