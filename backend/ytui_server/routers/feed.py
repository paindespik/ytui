"""Feed and search endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from ..models import FeedResponse, SearchResponse
from ..services import crowdbunker, odysee, ytdlp

router = APIRouter()


@router.get("/feed", response_model=FeedResponse)
async def get_feed(request: Request, refresh: bool = False) -> FeedResponse:
    result = await request.app.state.feed_service.feed(force_refresh=refresh)
    return FeedResponse(videos=result.videos, warnings=result.warnings)


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    source: Literal["youtube", "odysee", "crowdbunker"] = "youtube",
) -> SearchResponse:
    try:
        if source == "odysee":
            items = await odysee.search(q, limit=limit)
        elif source == "crowdbunker":
            items = await crowdbunker.search(q, limit=limit)
        else:
            items = await ytdlp.search_videos(q, limit=limit)
    except (
        ytdlp.UpstreamError,
        odysee.OdyseeError,
        crowdbunker.CrowdBunkerError,
    ) as exc:
        raise HTTPException(status_code=502, detail=f"Search failed: {exc}") from exc
    return SearchResponse(items=items)
