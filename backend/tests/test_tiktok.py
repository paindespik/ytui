"""TikTok: username parsing, live detection, monitor merge, /streams resolution."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ytui_server.models import FollowedChannel, StreamInfo, Video
from ytui_server.services import tiktok, ytdlp

LIVE_PAGE = (
    '<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">'
    '<meta name="title" content="YT Live">'
    '"isLive": true'
)

USER_NOT_FOUND = {"data": None, "message": "user_not_found", "statusCode": 19881007}


def _user_room(status=2, room_id="7551234567890123456", nickname="Weather News"):
    """api-live/user/room payload (verified shape, reduced)."""
    return {
        "statusCode": 0,
        "data": {
            "user": {
                "roomId": room_id,
                "nickname": nickname,
                "uniqueId": "weathernewslive",
                "secUid": "MS4wLjABAAAA_secuid",
            },
            "liveRoom": {
                "status": status,
                "title": "Morning forecast",
                "coverUrl": "https://p16-sign.tiktokcdn.com/cover.jpg",
                "startTime": 1752969600,
            },
            "stats": {"followerCount": 1},
        },
    }


def _room_info(status=2, hls="", hls_map=None, flv=None):
    """webcast/room/info payload (verified shape, reduced)."""
    return {
        "data": {
            "status": status,
            "title": "Morning forecast",
            "owner": {"nickname": "Weather News", "display_id": "weathernewslive"},
            "stream_url": {
                "hls_pull_url": hls,
                "hls_pull_url_map": hls_map or {},
                "flv_pull_url": flv or {},
            },
        },
        "extra": {},
    }


def _get_json(payload):
    async def fake(url, **kwargs):
        return httpx.Response(200, json=payload, request=httpx.Request("GET", str(url)))

    return fake


def _get_router(routes, default_text=None):
    """Dispatch GETs on a URL substring: {needle: json_payload}."""

    async def fake(url, **kwargs):
        u = str(url)
        for needle, payload in routes.items():
            if needle in u:
                return httpx.Response(200, json=payload, request=httpx.Request("GET", u))
        if default_text is not None:
            return httpx.Response(200, text=default_text, request=httpx.Request("GET", u))
        raise AssertionError(f"unexpected GET {u}")

    return fake


def _tiktok_channel(username: str = "weathernewslive") -> FollowedChannel:
    return FollowedChannel(
        ref=f"tiktok:{username}", channel_id=username, title="Weather", platform="tiktok"
    )


# ─── username parsing & URLs ───


def test_parse_username_prefix_and_url():
    assert tiktok.parse_username("tiktok:WeatherNewsLive") == "weathernewslive"
    assert tiktok.parse_username("tiktok:@user.name_1") == "user.name_1"
    assert tiktok.parse_username("https://www.tiktok.com/@WeatherNewsLive") == "weathernewslive"
    assert tiktok.parse_username("https://tiktok.com/@foo/live") == "foo"
    # Bare names never resolve to TikTok (YouTube/Twitch fallback chain).
    assert tiktok.parse_username("weathernewslive") is None
    assert tiktok.parse_username("UCtest000000000000000000") is None


def test_parse_username_rejects_invalid():
    with pytest.raises(ValueError):
        tiktok.parse_username("tiktok:bad!name")
    with pytest.raises(ValueError):
        tiktok.parse_username("tiktok:" + "a" * 25)
    with pytest.raises(ValueError):
        tiktok.parse_username("tiktok:")


def test_tiktok_video_url_forms():
    live = Video(video_id="weathernewslive:755", title="", platform="tiktok")
    assert live.url == "https://www.tiktok.com/@weathernewslive/live"
    channel = Video(video_id="weathernewslive", title="", kind="channel", platform="tiktok")
    assert channel.url == "https://www.tiktok.com/@weathernewslive"
    vid = Video(video_id="7300000000", title="", channel_id="user", platform="tiktok")
    assert vid.url == "https://www.tiktok.com/@user/video/7300000000"
    bare = Video(video_id="7300000000", title="", platform="tiktok")
    assert bare.url == "https://www.tiktok.com/embed/7300000000"


# ─── check_live ───


def _run_check_live(payload, username="weathernewslive"):
    async def run():
        async with httpx.AsyncClient() as client:
            with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=_get_json(payload))):
                return await tiktok.check_live(client, username)

    return asyncio.run(run())


def test_check_live_live():
    video = _run_check_live(_user_room())
    assert video is not None
    assert video.video_id == "weathernewslive:7551234567890123456"
    assert video.platform == "tiktok"
    assert video.title == "Morning forecast"
    assert video.channel_id == "weathernewslive"
    assert video.channel_title == "Weather News"
    assert video.published is not None
    assert video.thumbnail_url.startswith("https://p16-sign.tiktokcdn.com/")


def test_check_live_offline():
    assert _run_check_live(_user_room(status=4)) is None


def test_check_live_missing_room_id():
    assert _run_check_live(_user_room(room_id="")) is None


def test_check_live_unknown_user():
    with pytest.raises(tiktok.TikTokError):
        _run_check_live(USER_NOT_FOUND)


def test_fetch_user_room_malformed_payload():
    with pytest.raises(tiktok.TikTokError):
        _run_check_live({"data": "nope"})


# ─── stream URL preference ───


def test_pick_stream_url_preference():
    assert tiktok._pick_stream_url({"hls_pull_url": "https://hls"}) == "https://hls"
    assert (
        tiktok._pick_stream_url({"hls_pull_url": "", "hls_pull_url_map": {"q": "https://map"}})
        == "https://map"
    )
    flv = {"SD1": "https://sd1", "FULL_HD1": "https://fhd", "HD1": "https://hd1"}
    assert tiktok._pick_stream_url({"flv_pull_url": flv}) == "https://fhd"
    assert (
        tiktok._pick_stream_url({"flv_pull_url": {"SD1": "https://sd1", "SD2": "https://sd2"}})
        == "https://sd2"
    )
    assert tiktok._pick_stream_url({"flv_pull_url": {"XY": "https://xy"}}) == "https://xy"
    assert tiktok._pick_stream_url({}) == ""


def _run_resolve(room_info_payload):
    routes = {
        "api-live/user/room": _user_room(),
        "webcast/room/info": room_info_payload,
    }

    async def run():
        async with httpx.AsyncClient() as client:
            with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=_get_router(routes))):
                return await tiktok.resolve_live_stream(client, "weathernewslive")

    return asyncio.run(run())


def test_resolve_live_stream_flv_only():
    resolved = _run_resolve(
        _room_info(flv={"HD1": "https://pull-flv/hd1.flv", "SD1": "https://pull-flv/sd1.flv"})
    )
    assert resolved == ("https://pull-flv/hd1.flv", "Morning forecast")


def test_resolve_live_stream_prefers_hls():
    resolved = _run_resolve(
        _room_info(hls="https://pull-hls/index.m3u8", flv={"HD1": "https://pull-flv/hd1.flv"})
    )
    assert resolved == ("https://pull-hls/index.m3u8", "Morning forecast")


def test_resolve_live_stream_dead_room():
    assert _run_resolve(_room_info(status=4, flv={"HD1": "https://x.flv"})) is None


# ─── monitor merge semantics ───


def test_monitor_merges_youtube_and_tiktok(client, app):
    db = app.state.db
    db.add_channel(
        FollowedChannel(ref="UCtest000000000000000000", channel_id="UCtest000000000000000000")
    )
    db.add_channel(_tiktok_channel())

    both = _get_router({"api-live/user/room": _user_room()}, default_text=LIVE_PAGE)
    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=both)):
        asyncio.run(app.state.live_monitor.check_once())
        asyncio.run(app.state.live_monitor.check_tiktok_once())

    lives = client.get("/api/lives").json()
    assert sorted(e["video"]["platform"] for e in lives) == ["tiktok", "youtube"]

    # The TikTok loop must not clobber YouTube entries when TikTok goes offline.
    offline = _get_router({"api-live/user/room": _user_room(status=4)}, default_text=LIVE_PAGE)
    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=offline)):
        asyncio.run(app.state.live_monitor.check_tiktok_once())
    lives = client.get("/api/lives").json()
    assert [e["video"]["platform"] for e in lives] == ["youtube"]


def test_monitor_tiktok_detected_at_sticky_per_broadcast(client, app):
    app.state.db.add_channel(_tiktok_channel())
    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=_get_json(_user_room()))):
        asyncio.run(app.state.live_monitor.check_tiktok_once())
        first = client.get("/api/lives").json()[0]["detected_at"]
        asyncio.run(app.state.live_monitor.check_tiktok_once())
        assert client.get("/api/lives").json()[0]["detected_at"] == first

    # A new broadcast (new room id) resets detected_at.
    with patch.object(
        httpx.AsyncClient, "get", AsyncMock(side_effect=_get_json(_user_room(room_id="888")))
    ):
        asyncio.run(app.state.live_monitor.check_tiktok_once())
    lives = client.get("/api/lives").json()
    assert lives[0]["video"]["video_id"] == "weathernewslive:888"
    assert lives[0]["detected_at"] != first


def test_monitor_tiktok_per_channel_error_treated_offline(client, app):
    # Unlike the batched Twitch check (whole-batch transient failure keeps
    # state), a per-channel error counts as offline — the notify daemon's
    # 3-poll grace period absorbs blips.
    app.state.db.add_channel(_tiktok_channel())
    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=_get_json(_user_room()))):
        asyncio.run(app.state.live_monitor.check_tiktok_once())
    assert len(client.get("/api/lives").json()) == 1

    async def boom(url, **kwargs):
        raise httpx.ConnectError("down", request=httpx.Request("GET", str(url)))

    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=boom)):
        asyncio.run(app.state.live_monitor.check_tiktok_once())
    assert client.get("/api/lives").json() == []


# ─── follow endpoint ───


def test_follow_tiktok_channel(client):
    # Offline payload: following must not depend on the channel being live.
    with patch.object(
        httpx.AsyncClient, "get", AsyncMock(side_effect=_get_json(_user_room(status=4)))
    ):
        resp = client.post("/api/channels", json={"ref": "tiktok:WeatherNewsLive"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["channel_id"] == "weathernewslive"
    assert body["platform"] == "tiktok"
    assert body["title"] == "Weather News"
    assert body["ref"] == "tiktok:weathernewslive"


def test_follow_unknown_tiktok_channel_404(client):
    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=_get_json(USER_NOT_FOUND))):
        resp = client.post("/api/channels", json={"ref": "tiktok:zzqqxx__no_such_user"})
    assert resp.status_code == 404


def test_follow_invalid_tiktok_username_404(client):
    resp = client.post("/api/channels", json={"ref": "tiktok:not a user!"})
    assert resp.status_code == 404


# ─── /streams ───


def test_streams_tiktok_live_flv_only(client):
    routes = {
        "api-live/user/room": _user_room(),
        "webcast/room/info": _room_info(flv={"HD1": "https://pull-flv/hd1.flv"}),
    }
    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=_get_router(routes))):
        resp = client.get(
            "/api/videos/weathernewslive:7551234567890123456/streams",
            params={"platform": "tiktok"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "hls"
    assert body["url"] == "https://pull-flv/hd1.flv"
    assert body["title"] == "Morning forecast"


def test_streams_tiktok_live_falls_back_to_ytdlp(client):
    routes = {
        "api-live/user/room": _user_room(),
        "webcast/room/info": _room_info(status=4),
    }
    direct = AsyncMock(return_value=StreamInfo(kind="hls", url="https://direct/tt.m3u8"))
    with (
        patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=_get_router(routes))),
        patch.object(ytdlp, "resolve_streams", direct),
    ):
        resp = client.get(
            "/api/videos/weathernewslive:7551234567890123456/streams",
            params={"platform": "tiktok"},
        )
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://direct/tt.m3u8"
    assert direct.call_args.args[0] == "https://www.tiktok.com/@weathernewslive/live"
