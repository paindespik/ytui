"""Thumbnail fetcher tests: disk cache, in-flight dedup, LRU eviction."""

import asyncio
import os
from pathlib import Path

import httpx

from ytui.thumbnails.fetcher import ThumbnailFetcher, evict_lru

URL = "https://i.ytimg.com/vi/abc123/mqdefault.jpg"


def make_fetcher(tmp_path: Path, handler) -> tuple[ThumbnailFetcher, list]:
    requests: list[httpx.Request] = []

    def counting_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    fetcher = ThumbnailFetcher(directory=tmp_path / "thumbs")
    fetcher._client = httpx.AsyncClient(transport=httpx.MockTransport(counting_handler))
    return fetcher, requests


async def test_fetch_downloads_and_caches(tmp_path: Path):
    fetcher, requests = make_fetcher(tmp_path, lambda r: httpx.Response(200, content=b"jpegdata"))
    path = await fetcher.fetch(URL)
    assert path is not None and path.read_bytes() == b"jpegdata"
    assert fetcher.cached_path(URL) == path
    # Second fetch is served from disk, no new request.
    assert await fetcher.fetch(URL) == path
    assert len(requests) == 1
    await fetcher.close()


async def test_in_flight_dedup(tmp_path: Path):
    fetcher, requests = make_fetcher(tmp_path, lambda r: httpx.Response(200, content=b"x"))
    results = await asyncio.gather(fetcher.fetch(URL), fetcher.fetch(URL), fetcher.fetch(URL))
    assert all(r == results[0] for r in results)
    assert len(requests) == 1
    await fetcher.close()


async def test_fetch_failure_returns_none(tmp_path: Path):
    fetcher, _ = make_fetcher(tmp_path, lambda r: httpx.Response(404))
    assert await fetcher.fetch(URL) is None
    assert fetcher.cached_path(URL) is None
    await fetcher.close()


async def test_fetch_empty_url(tmp_path: Path):
    fetcher, requests = make_fetcher(tmp_path, lambda r: httpx.Response(200, content=b"x"))
    assert await fetcher.fetch("") is None
    assert not requests
    await fetcher.close()


def test_evict_lru_removes_oldest(tmp_path: Path):
    for i in range(5):
        f = tmp_path / f"{i}.jpg"
        f.write_bytes(b"x" * 10)
        os.utime(f, (i, i))  # atime = i (oldest first)
    evict_lru(tmp_path, max_bytes=30)
    remaining = sorted(f.name for f in tmp_path.glob("*.jpg"))
    assert remaining == ["2.jpg", "3.jpg", "4.jpg"]


def test_evict_lru_noop_under_limit(tmp_path: Path):
    (tmp_path / "a.jpg").write_bytes(b"x" * 10)
    evict_lru(tmp_path, max_bytes=100)
    assert (tmp_path / "a.jpg").exists()
