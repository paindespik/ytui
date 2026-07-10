"""Async thumbnail download with disk cache, in-flight dedup and LRU eviction."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx

from ..config import cache_dir

MAX_CACHE_BYTES = 100 * 1024 * 1024  # ~100 MB
TIMEOUT = httpx.Timeout(5.0)


def thumbs_dir() -> Path:
    return cache_dir() / "thumbs"


def _cache_path(url: str, directory: Path) -> Path:
    return directory / (hashlib.sha256(url.encode()).hexdigest() + ".jpg")


def evict_lru(directory: Path, max_bytes: int = MAX_CACHE_BYTES) -> None:
    """Delete least-recently-accessed thumbnails until the cache fits max_bytes."""
    files = []
    total = 0
    for f in directory.glob("*.jpg"):
        try:
            stat = f.stat()
        except OSError:
            continue
        files.append((stat.st_atime, stat.st_size, f))
        total += stat.st_size
    if total <= max_bytes:
        return
    files.sort()  # oldest access time first
    for _, size, f in files:
        try:
            f.unlink()
        except OSError:
            continue
        total -= size
        if total <= max_bytes:
            break


class ThumbnailFetcher:
    """Downloads thumbnails to a disk cache, deduplicating in-flight requests."""

    def __init__(self, directory: Path | None = None, max_bytes: int = MAX_CACHE_BYTES) -> None:
        self.directory = directory or thumbs_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self._in_flight: dict[str, asyncio.Task[Path | None]] = {}
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True)
        return self._client

    async def close(self) -> None:
        for task in self._in_flight.values():
            task.cancel()
        self._in_flight.clear()
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def cached_path(self, url: str) -> Path | None:
        """Return the cached file path if the thumbnail is already on disk."""
        path = _cache_path(url, self.directory)
        return path if path.exists() else None

    async def fetch(self, url: str) -> Path | None:
        """Return a local path for the thumbnail, downloading it if needed.

        Returns None on download failure. Concurrent calls for the same URL
        share a single download.
        """
        if not url:
            return None
        path = _cache_path(url, self.directory)
        if path.exists():
            return path
        task = self._in_flight.get(url)
        if task is None:
            task = asyncio.create_task(self._download(url, path))
            self._in_flight[url] = task
            task.add_done_callback(lambda _t: self._in_flight.pop(url, None))
        return await asyncio.shield(task)

    async def _download(self, url: str, path: Path) -> Path | None:
        try:
            resp = await self._get_client().get(url)
            resp.raise_for_status()
        except Exception:
            return None
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(resp.content)
        tmp.replace(path)
        evict_lru(self.directory, self.max_bytes)
        return path
