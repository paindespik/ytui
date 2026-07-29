"""Twitch: login parsing, GQL live batch, monitor merge, /streams proxying."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ytui_server.models import FollowedChannel, StreamInfo, Video
from ytui_server.services import twitch, ytdlp

LIVE_PAGE = (
    '<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">'
    '<meta name="title" content="YT Live">'
    '"isLive": true'
)

GQL_LIVE = [
    {
        "data": {
            "users": [
                {
                    "login": "foo",
                    "displayName": "Foo",
                    "stream": {
                        "id": "111",
                        "title": "Foo live title",
                        "type": "live",
                        "viewersCount": 42,
                        "createdAt": "2026-07-19T20:00:00Z",
                    },
                },
                {"login": "bar", "displayName": "Bar", "stream": None},
            ]
        }
    }
]
GQL_OFFLINE = [{"data": {"users": [{"login": "foo", "displayName": "Foo", "stream": None}]}}]
GQL_UNKNOWN = [{"data": {"users": [None]}}]


def _post_json(payload):
    async def fake(url, **kwargs):
        return httpx.Response(200, json=payload, request=httpx.Request("POST", str(url)))

    return fake


def _twitch_channel(login: str = "foo") -> FollowedChannel:
    return FollowedChannel(
        ref=f"twitch:{login}", channel_id=login, title=login.title(), platform="twitch"
    )


# ─── login parsing & URLs ───


def test_parse_login_prefix_and_url():
    assert twitch.parse_login("twitch:ZeratoR") == "zerator"
    assert twitch.parse_login("https://www.twitch.tv/ZeratoR") == "zerator"
    assert twitch.parse_login("twitch.tv/foo_bar") == "foo_bar"
    assert twitch.parse_login("@somehandle") is None
    assert twitch.parse_login("UCtest000000000000000000") is None


def test_parse_login_rejects_invalid_and_reserved():
    with pytest.raises(ValueError):
        twitch.parse_login("twitch:no way!")
    with pytest.raises(ValueError):
        twitch.parse_login("twitch:ab")  # too short
    with pytest.raises(ValueError):
        twitch.parse_login("https://www.twitch.tv/videos")


def test_twitch_video_url_forms():
    live = Video(video_id="foo:111", title="", platform="twitch")
    assert live.url == "https://www.twitch.tv/foo"
    vod = Video(video_id="v246974233", title="", platform="twitch")
    assert vod.url == "https://www.twitch.tv/videos/246974233"
    channel = Video(video_id="foo", title="", kind="channel", platform="twitch")
    assert channel.url == "https://www.twitch.tv/foo"


# ─── GQL batch ───


def test_check_live_batch():
    async def run():
        async with httpx.AsyncClient() as client:
            with patch.object(
                httpx.AsyncClient, "post", AsyncMock(side_effect=_post_json(GQL_LIVE))
            ):
                return await twitch.check_live_batch(client, ["foo", "bar"])

    live = asyncio.run(run())
    assert set(live) == {"foo"}
    video = live["foo"]
    assert video.video_id == "foo:111"
    assert video.platform == "twitch"
    assert video.title == "Foo live title"
    assert video.channel_id == "foo"
    assert video.channel_title == "Foo"
    assert video.published is not None
    assert "live_user_foo" in video.thumbnail_url


def test_check_live_batch_malformed_payload():
    async def run():
        async with httpx.AsyncClient() as client:
            with patch.object(
                httpx.AsyncClient, "post", AsyncMock(side_effect=_post_json({"oops": 1}))
            ):
                return await twitch.check_live_batch(client, ["foo"])

    with pytest.raises(twitch.TwitchError):
        asyncio.run(run())


# ─── monitor merge semantics ───


def test_monitor_merges_youtube_and_twitch(client, app):
    db = app.state.db
    db.add_channel(
        FollowedChannel(ref="UCtest000000000000000000", channel_id="UCtest000000000000000000")
    )
    db.add_channel(_twitch_channel())

    async def live_page(url, **kwargs):
        return httpx.Response(200, text=LIVE_PAGE, request=httpx.Request("GET", str(url)))

    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=live_page)):
        asyncio.run(app.state.live_monitor.check_once())
    with patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=_post_json(GQL_LIVE))):
        asyncio.run(app.state.live_monitor.check_twitch_once())

    lives = client.get("/api/lives").json()
    assert sorted(e["video"]["platform"] for e in lives) == ["twitch", "youtube"]

    # The Twitch loop must not clobber YouTube entries when Twitch goes offline.
    with patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=_post_json(GQL_OFFLINE))):
        asyncio.run(app.state.live_monitor.check_twitch_once())
    lives = client.get("/api/lives").json()
    assert [e["video"]["platform"] for e in lives] == ["youtube"]


def test_monitor_twitch_keeps_state_on_transport_error(client, app):
    app.state.db.add_channel(_twitch_channel())
    with patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=_post_json(GQL_LIVE))):
        asyncio.run(app.state.live_monitor.check_twitch_once())
    assert len(client.get("/api/lives").json()) == 1

    async def boom(url, **kwargs):
        raise httpx.ConnectError("down", request=httpx.Request("POST", str(url)))

    with patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=boom)):
        asyncio.run(app.state.live_monitor.check_twitch_once())
    assert len(client.get("/api/lives").json()) == 1


def test_monitor_twitch_detected_at_sticky_per_broadcast(client, app):
    app.state.db.add_channel(_twitch_channel())
    with patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=_post_json(GQL_LIVE))):
        asyncio.run(app.state.live_monitor.check_twitch_once())
        first = client.get("/api/lives").json()[0]["detected_at"]
        asyncio.run(app.state.live_monitor.check_twitch_once())
        assert client.get("/api/lives").json()[0]["detected_at"] == first

    # A new broadcast (new stream id) resets detected_at.
    new_broadcast = [
        {
            "data": {
                "users": [
                    {
                        "login": "foo",
                        "displayName": "Foo",
                        "stream": {"id": "222", "title": "Again", "type": "live"},
                    }
                ]
            }
        }
    ]
    with patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=_post_json(new_broadcast))):
        asyncio.run(app.state.live_monitor.check_twitch_once())
    lives = client.get("/api/lives").json()
    assert lives[0]["video"]["video_id"] == "foo:222"
    assert lives[0]["detected_at"] != first


# ─── follow endpoint ───


def test_follow_twitch_channel(client):
    payload = [{"data": {"users": [{"login": "foo4", "displayName": "Foo", "stream": None}]}}]
    with patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=_post_json(payload))):
        resp = client.post("/api/channels", json={"ref": "twitch:Foo4"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["channel_id"] == "foo4"
    assert body["platform"] == "twitch"
    assert body["title"] == "Foo"


def test_follow_unknown_twitch_channel_404(client):
    with patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=_post_json(GQL_UNKNOWN))):
        resp = client.post("/api/channels", json={"ref": "twitch:nobody"})
    assert resp.status_code == 404


# ─── /streams ───


def test_streams_twitch_live_via_proxy(client):
    async def playlist(url, **kwargs):
        return httpx.Response(
            200, text="#EXTM3U\n#EXT-X-VERSION:3", request=httpx.Request("GET", str(url))
        )

    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=playlist)):
        resp = client.get("/api/videos/foo:111/streams", params={"platform": "twitch"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "hls"
    assert body["url"].startswith("https://eu.luminous.dev/live/foo")


MASTER = (
    "#EXTM3U\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080,VIDEO="chunked"\n'
    "https://cdn.ttvnw.net/1080.m3u8\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720,VIDEO="720p60"\n'
    "https://cdn.ttvnw.net/720.m3u8\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=200000,RESOLUTION=284x160,VIDEO="160p"\n'
    "https://cdn.ttvnw.net/160.m3u8\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=160000,CODECS="mp4a.40.2",VIDEO="audio_only"\n'
    "https://cdn.ttvnw.net/audio.m3u8\n"
)


def test_streams_twitch_live_capped(client):
    async def master(url, **kwargs):
        return httpx.Response(200, text=MASTER, request=httpx.Request("GET", str(url)))

    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=master)):
        resp = client.get(
            "/api/videos/foo:111/streams",
            params={"platform": "twitch", "max_height": 720},
        )
    body = resp.json()
    # Capping means handing over one variant instead of the master playlist.
    assert (body["url"], body["height"]) == ("https://cdn.ttvnw.net/720.m3u8", 720)

    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=master)):
        resp = client.get("/api/videos/foo:111/streams", params={"platform": "twitch"})
    body = resp.json()
    assert body["url"].startswith("https://eu.luminous.dev/live/foo")
    assert body["height"] == 1080


def test_streams_twitch_live_falls_back_to_direct(client):
    async def dead(url, **kwargs):
        return httpx.Response(404, request=httpx.Request("GET", str(url)))

    direct = AsyncMock(return_value=StreamInfo(kind="hls", url="https://direct/foo.m3u8"))
    with (
        patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=dead)),
        patch.object(ytdlp, "resolve_streams", direct),
    ):
        resp = client.get("/api/videos/foo:111/streams", params={"platform": "twitch"})
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://direct/foo.m3u8"
    assert direct.call_args.args[0] == "https://www.twitch.tv/foo"


def test_streams_twitch_vod_skips_proxy(client):
    direct = AsyncMock(return_value=StreamInfo(kind="hls", url="https://vod/x.m3u8"))
    with patch.object(ytdlp, "resolve_streams", direct):
        resp = client.get("/api/videos/v246974233/streams", params={"platform": "twitch"})
    assert resp.status_code == 200
    assert direct.call_args.args[0] == "https://www.twitch.tv/videos/246974233"


# ─── VOD listing platform inference ───


def test_entry_to_item_twitch_platform():
    entry = {
        "id": "v246974233",
        "url": "https://www.twitch.tv/videos/246974233",
        "title": "VOD title",
        "uploader": "Foo",
    }
    item = ytdlp.entry_to_item(entry)
    assert item is not None
    assert item.platform == "twitch"
    assert item.url == "https://www.twitch.tv/videos/246974233"
