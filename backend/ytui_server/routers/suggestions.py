"""History-based suggestions endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..models import FeedResponse

router = APIRouter()


@router.get("/suggestions", response_model=FeedResponse)
async def get_suggestions(request: Request, refresh: bool = False) -> FeedResponse:
    result = await request.app.state.suggestions_service.suggestions(force_refresh=refresh)
    return FeedResponse(videos=result.videos, warnings=result.warnings)
