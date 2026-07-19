"""Followed channels CRUD (server-side replacement for [channels].list)."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request

from ..models import ChannelIn, FollowedChannel
from ..services.twitch import TwitchError

router = APIRouter()


@router.get("/channels", response_model=list[FollowedChannel])
async def list_channels(request: Request) -> list[FollowedChannel]:
    return request.app.state.db.list_channels()


@router.post("/channels", response_model=FollowedChannel, status_code=201)
async def add_channel(body: ChannelIn, request: Request) -> FollowedChannel:
    ref = body.ref.strip()
    if not ref:
        raise HTTPException(status_code=422, detail="Empty channel reference")
    feed_service = request.app.state.feed_service
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        try:
            channel = await feed_service.resolve_ref(ref, client)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (httpx.HTTPError, TwitchError) as exc:
            raise HTTPException(
                status_code=502, detail=f"Channel resolution failed: {exc}"
            ) from exc
    if not request.app.state.db.add_channel(channel):
        raise HTTPException(status_code=409, detail="Channel already followed")
    return channel


@router.delete("/channels/{channel_id}", status_code=204)
async def remove_channel(channel_id: str, request: Request) -> None:
    if not request.app.state.db.remove_channel(channel_id):
        raise HTTPException(status_code=404, detail="Channel not followed")
