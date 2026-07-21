"""Current live streams of followed channels (clients poll this)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..models import ChatResponse, LiveOut

router = APIRouter()


@router.get("/lives", response_model=list[LiveOut])
async def get_lives(request: Request) -> list[LiveOut]:
    monitor = request.app.state.live_monitor
    return [
        LiveOut(video=video, detected_at=detected_at)
        for video, detected_at in monitor.lives.values()
    ]


@router.get("/lives/{video_id}/chat", response_model=ChatResponse)
async def live_chat(
    video_id: str, request: Request, platform: str = "youtube", cursor: int = 0
) -> ChatResponse:
    if platform not in ("youtube", "twitch"):
        raise HTTPException(
            status_code=501,
            detail="Live chat is only available for YouTube and Twitch",
        )
    return await request.app.state.chat_manager.poll(platform, video_id, cursor)
