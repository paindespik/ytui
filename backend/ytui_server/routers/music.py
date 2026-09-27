"""YouTube Music home endpoint (needs the account cookies)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..models import MusicHomeResponse

router = APIRouter()


@router.get("/music", response_model=MusicHomeResponse)
async def get_music_home(request: Request, refresh: bool = False) -> MusicHomeResponse:
    result = await request.app.state.music_service.home(force_refresh=refresh)
    return MusicHomeResponse(shelves=result.shelves, warnings=result.warnings)
