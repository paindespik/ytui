"""Watch history endpoints (shared across devices)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from ..models import HistoryEntry, HistoryIn, PositionIn, ResumeOut, WatchedIdsOut

router = APIRouter()


@router.get("/history", response_model=list[HistoryEntry])
async def get_history(
    request: Request, limit: int = Query(default=200, ge=1, le=1000)
) -> list[HistoryEntry]:
    rows = request.app.state.db.watch_history(limit=limit)
    return [
        HistoryEntry(video=video, watched_at=watched_at, position=position)
        for video, watched_at, position in rows
    ]


@router.post("/history", status_code=204)
async def record_watch(body: HistoryIn, request: Request) -> None:
    request.app.state.db.record_watch(body.video)


@router.get("/history/watched-ids", response_model=WatchedIdsOut)
async def watched_ids(request: Request) -> WatchedIdsOut:
    return WatchedIdsOut(ids=request.app.state.db.watched_ids())


@router.put("/history/{video_id}/position", status_code=204)
async def save_position(video_id: str, body: PositionIn, request: Request) -> None:
    if not request.app.state.db.save_position(video_id, body.position, body.duration):
        raise HTTPException(status_code=404, detail="Video not in history")


@router.get("/history/{video_id}/resume", response_model=ResumeOut)
async def get_resume(video_id: str, request: Request) -> ResumeOut:
    row = request.app.state.db.get_resume(video_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Video not in history")
    position, duration, playlist_id = row
    return ResumeOut(position=position, duration=duration, playlist_id=playlist_id)


@router.delete("/history/{video_id}", status_code=204)
async def remove_watch(video_id: str, request: Request) -> None:
    if not request.app.state.db.remove_watch(video_id):
        raise HTTPException(status_code=404, detail="Video not in history")
