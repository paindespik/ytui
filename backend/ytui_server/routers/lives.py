"""Current live streams of followed channels (clients poll this)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..models import LiveOut

router = APIRouter()


@router.get("/lives", response_model=list[LiveOut])
async def get_lives(request: Request) -> list[LiveOut]:
    monitor = request.app.state.live_monitor
    return [
        LiveOut(video=video, detected_at=detected_at)
        for video, detected_at in monitor.lives.values()
    ]
