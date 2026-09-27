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


def test_parse_live_page_author_fallback():
    page = LIVE_PAGE + ' "author":"Speak \\u0026 Machine"'
    video = parse_live_page(page, "UCx")
    assert video is not None
    assert video.channel_title == "Speak & Machine"


def test_parse_live_page_author_does_not_override_known_title():
    page = LIVE_PAGE + ' "author":"Someone Else"'
    video = parse_live_page(page, "UCx", "Chan")
    assert video is not None
    assert video.channel_title == "Chan"


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


def test_same_login_live_on_twitch_and_tiktok(client, app):
    """Both lives are listed: the monitor keys entries by platform too."""
    import asyncio

    from ytui_server.models import Video
    from ytui_server.services import live as live_mod

    db = app.state.db
    db.add_channel(FollowedChannel(ref="twitch:samelogin", channel_id="samelogin", platform="twitch"))
    db.add_channel(FollowedChannel(ref="tiktok:samelogin", channel_id="samelogin", platform="tiktok"))

    async def twitch_batch(client, logins):
        return {"samelogin": Video(video_id="samelogin:1", title="t", platform="twitch")}

    async def tiktok_live(client, username, title=""):
        return Video(video_id="samelogin:2", title="k", platform="tiktok")

    monitor = app.state.live_monitor
    with patch.object(live_mod.twitch, "check_live_batch", twitch_batch), patch.object(
        live_mod.tiktok, "check_live", tiktok_live
    ):
        asyncio.run(monitor.check_twitch_once())
        asyncio.run(monitor.check_tiktok_once())
    ids = sorted(item["video"]["video_id"] for item in client.get("/api/lives").json())
    assert ids == ["samelogin:1", "samelogin:2"]
