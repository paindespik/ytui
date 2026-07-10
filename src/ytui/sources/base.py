"""Abstract video source interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Video


class FeedResult:
    """Feed videos plus non-fatal warnings (failed channels, offline fallback)."""

    def __init__(self, videos: list[Video], warnings: list[str] | None = None) -> None:
        self.videos = videos
        self.warnings = warnings or []


class VideoSource(ABC):
    @abstractmethod
    async def feed(self, force_refresh: bool = False) -> FeedResult:
        """Return the merged, date-sorted feed."""

    @abstractmethod
    async def search(self, query: str, limit: int = 20) -> list[Video]:
        """Search for videos."""
