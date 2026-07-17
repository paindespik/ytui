"""Server status: version, uptime, DB size and cache freshness (diagnostics)."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

from .. import __version__
from ..models import StatusOut

router = APIRouter()


@router.get("/status", response_model=StatusOut)
async def status(request: Request) -> StatusOut:
    stats = request.app.state.db.stats()
    now = time.time()
    newest = stats["feed_newest_fetched_at"]
    oldest = stats["feed_oldest_fetched_at"]
    return StatusOut(
        version=__version__,
        uptime_seconds=time.monotonic() - request.app.state.started_at,
        db_bytes=stats["db_bytes"],
        history_rows=stats["history_rows"],
        channels=stats["channels"],
        feed_cached_channels=stats["feed_cached_channels"],
        feed_newest_age_seconds=None if newest is None else now - newest,
        feed_oldest_age_seconds=None if oldest is None else now - oldest,
        lives_active=len(request.app.state.live_monitor.lives),
    )
