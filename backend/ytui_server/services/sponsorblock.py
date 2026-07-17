"""SponsorBlock lookup with DB cache.

The server queries sponsor.ajay.app and caches per-video segments; clients
fetch the cached segments and skip locally. A SponsorBlock failure must NEVER
prevent playback: errors degrade to stale cache or an empty list.
"""

from __future__ import annotations

import json
import logging

import httpx

from ..db import Database
from ..models import SponsorSegment

log = logging.getLogger(__name__)

SPONSORBLOCK_API = "https://sponsor.ajay.app/api/skipSegments"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ytui-server/0.2"


class SponsorBlockService:
    def __init__(
        self, db: Database, categories: list[str], ttl_seconds: int = 24 * 3600
    ) -> None:
        self.db = db
        self.categories = categories
        self.ttl_seconds = ttl_seconds

    async def get(self, video_id: str) -> list[SponsorSegment]:
        """Skip segments for a video, cached. Empty categories disable lookups."""
        if not self.categories:
            return []
        cached = self.db.get_sponsor_segments(video_id, self.ttl_seconds)
        if cached is not None:
            return [SponsorSegment.model_validate(s) for s in cached]
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.get(
                    SPONSORBLOCK_API,
                    params={
                        "videoID": video_id,
                        "categories": json.dumps(self.categories),
                    },
                    headers={"User-Agent": USER_AGENT},
                )
            if resp.status_code == 404:
                # No segments for this video: cache the empty result.
                self.db.set_sponsor_segments(video_id, [])
                return []
            resp.raise_for_status()
            segments = [
                SponsorSegment(
                    category=entry.get("category") or "",
                    start=float(entry["segment"][0]),
                    end=float(entry["segment"][1]),
                )
                for entry in resp.json()
                if entry.get("actionType") == "skip"
                and float(entry["segment"][1]) > float(entry["segment"][0])
            ]
        except Exception as exc:
            log.warning("sponsorblock lookup failed for %s: %s", video_id, exc)
            stale = self.db.get_sponsor_segments(
                video_id, self.ttl_seconds, allow_stale=True
            )
            if stale is not None:
                return [SponsorSegment.model_validate(s) for s in stale]
            return []
        segments.sort(key=lambda s: s.start)
        self.db.set_sponsor_segments(
            video_id, [s.model_dump(mode="json") for s in segments]
        )
        return segments
