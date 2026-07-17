"""SponsorBlock service (cache, degradation) and /sponsor endpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from ytui_server.db import Database
from ytui_server.services import sponsorblock
from ytui_server.services.sponsorblock import SponsorBlockService

CATEGORIES = ["sponsor", "selfpromo"]


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://sponsor.ajay.app")
            raise httpx.HTTPStatusError(
                "boom",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )

    def json(self):
        return self._payload


class FakeAsyncClient:
    calls = 0

    def __init__(self, response=None, error=None, **kwargs):
        self._response = response
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, headers=None):
        type(self).calls += 1
        if self._error is not None:
            raise self._error
        return self._response


@pytest.fixture()
def db(tmp_path: Path):
    database = Database(tmp_path / "meta.sqlite")
    yield database
    database.close()


def _patched(response=None, error=None):
    class Client(FakeAsyncClient):
        calls = 0

    return (
        patch.object(
            sponsorblock.httpx, "AsyncClient", lambda **kw: Client(response, error)
        ),
        Client,
    )


async def test_filters_sorts_and_caches(db):
    payload = [
        {"category": "selfpromo", "actionType": "skip", "segment": [60.0, 90.0]},
        {"category": "sponsor", "actionType": "mute", "segment": [5.0, 10.0]},
        {"category": "sponsor", "actionType": "skip", "segment": [10.0, 42.5]},
        {"category": "sponsor", "actionType": "skip", "segment": [99.0, 99.0]},
    ]
    service = SponsorBlockService(db, categories=CATEGORIES)
    ctx, client_cls = _patched(response=FakeResponse(payload=payload))
    with ctx:
        segments = await service.get("vid000000001")
        again = await service.get("vid000000001")
    assert [(s.category, s.start, s.end) for s in segments] == [
        ("sponsor", 10.0, 42.5),
        ("selfpromo", 60.0, 90.0),
    ]
    assert again == segments
    assert client_cls.calls == 1  # second call served from cache


async def test_404_caches_empty(db):
    service = SponsorBlockService(db, categories=CATEGORIES)
    ctx, client_cls = _patched(response=FakeResponse(status_code=404))
    with ctx:
        assert await service.get("vid000000001") == []
        assert await service.get("vid000000001") == []
    assert client_cls.calls == 1


async def test_network_error_returns_stale(db):
    db.set_sponsor_segments(
        "vid000000001", [{"category": "sponsor", "start": 1.0, "end": 2.0}]
    )
    service = SponsorBlockService(db, categories=CATEGORIES, ttl_seconds=0)
    ctx, _ = _patched(error=httpx.ConnectError("down"))
    with ctx:
        segments = await service.get("vid000000001")
    assert [(s.start, s.end) for s in segments] == [(1.0, 2.0)]


async def test_network_error_without_cache_returns_empty(db):
    service = SponsorBlockService(db, categories=CATEGORIES)
    ctx, _ = _patched(error=httpx.ConnectError("down"))
    with ctx:
        assert await service.get("vid000000001") == []
    # Failure must not be cached: nothing stored for the video.
    assert db.get_sponsor_segments("vid000000001", 3600, allow_stale=True) is None


async def test_empty_categories_skip_http(db):
    service = SponsorBlockService(db, categories=[])
    ctx, client_cls = _patched(response=FakeResponse(payload=[]))
    with ctx:
        assert await service.get("vid000000001") == []
    assert client_cls.calls == 0


def test_endpoint_non_youtube_returns_empty(client):
    resp = client.get("/api/videos/vid000000001/sponsor", params={"platform": "odysee"})
    assert resp.status_code == 200
    assert resp.json() == {"segments": []}


def test_endpoint_returns_segments(client, app):
    app.state.db.set_sponsor_segments(
        "vid000000001", [{"category": "sponsor", "start": 3.0, "end": 9.0}]
    )
    resp = client.get("/api/videos/vid000000001/sponsor")
    assert resp.status_code == 200
    assert resp.json()["segments"] == [{"category": "sponsor", "start": 3.0, "end": 9.0}]
