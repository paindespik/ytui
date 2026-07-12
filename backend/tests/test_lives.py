"""Live detection: page parsing, monitor polling and /api/lives."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx

from ytui_server.models import FollowedChannel
from ytui_server.services.live import parse_live_page

LIVE_PAGE = (
    '<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">'
    '<meta name="title" content="My &amp; Live">'
    '"isLive": true'
)
IDLE_PAGE = (
    '<link rel="canonical" href="https://www.youtube.com/channel/UCtest000000000000000000">'
)
UPCOMING_PAGE = (
    '<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">'
    '"isLive": true "isUpcoming": true'
)


def test_parse_live_page_live():
    video = parse_live_page(LIVE_PAGE, "UCx", "Chan")
    assert video is not None
    assert video.video_id == "abcdefghijk"
    assert video.title == "My & Live"
    assert video.channel_id == "UCx"


def test_parse_live_page_idle():
    assert parse_live_page(IDLE_PAGE, "UCx") is None


def test_parse_live_page_upcoming():
    assert parse_live_page(UPCOMING_PAGE, "UCx") is None


def test_lives_empty(client):
    assert client.get("/api/lives").json() == []


def test_lives_after_check(client, app):
    app.state.db.add_channel(
        FollowedChannel(ref="UCtest000000000000000000", channel_id="UCtest000000000000000000")
    )

    async def fake_get(url, **kwargs):
        return httpx.Response(200, text=LIVE_PAGE, request=httpx.Request("GET", str(url)))

    import asyncio

    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=fake_get)):
        asyncio.run(app.state.live_monitor.check_once())

    lives = client.get("/api/lives").json()
    assert len(lives) == 1
    assert lives[0]["video"]["video_id"] == "abcdefghijk"
    assert lives[0]["detected_at"]


def test_lives_cleared_when_offline_check_fails(client, app):
    import asyncio

    app.state.db.add_channel(
        FollowedChannel(ref="UCtest000000000000000000", channel_id="UCtest000000000000000000")
    )

    async def live_get(url, **kwargs):
        return httpx.Response(200, text=LIVE_PAGE, request=httpx.Request("GET", str(url)))

    async def idle_get(url, **kwargs):
        return httpx.Response(200, text=IDLE_PAGE, request=httpx.Request("GET", str(url)))

    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=live_get)):
        asyncio.run(app.state.live_monitor.check_once())
    assert len(client.get("/api/lives").json()) == 1

    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=idle_get)):
        asyncio.run(app.state.live_monitor.check_once())
    assert client.get("/api/lives").json() == []
