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


# ─── preroll avoidance ───

# Shapes measured on a direct (ad-fed) French token, 2026-10-02.
AD_MEDIA = (
    "#EXTM3U\n#EXT-X-VERSION:6\n#EXT-X-TARGETDURATION:5\n#EXT-X-MEDIA-SEQUENCE:0\n"
    '#EXT-X-DATERANGE:ID="stitched-ad-1790942337-30235000000",CLASS="twitch-stitched-ad",'
    'START-DATE="2026-10-02T11:58:57.799Z",DURATION=30.235\n'
    "#EXT-X-DISCONTINUITY\n"
    "#EXTINF:2.000,Amazon|2474283100494\nhttps://edge.hls.ttvnw.net/v1/segment/ad.mp4\n"
)
LIVE_MEDIA = (
    "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:2888\n"
    '#EXT-X-DATERANGE:ID="source-1790941910",CLASS="twitch-stream-source",'
    'START-DATE="2026-10-02T11:51:50.356Z",END-ON-NEXT=YES,X-TV-TWITCH-STREAM-SOURCE="live"\n'
    "#EXTINF:4.167,live\nhttps://edge.hls.ttvnw.net/v1/segment/live.ts\n"
)


def _proxy_world(prerolls: dict[str, list[bool]]):
    """MockTransport where each proxy host serves masters whose token draws a
    preroll or not, in order (last draw repeats); returns (client, hits)."""
    hits: list[str] = []
    draws = {host: list(seq) for host, seq in prerolls.items()}

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        hits.append(f"{host}{request.url.path}")
        if host in draws:
            seq = draws[host]
            ad = seq.pop(0) if len(seq) > 1 else seq[0]
            tag = "ad" if ad else "clean"
            return httpx.Response(200, text=MASTER.replace("cdn.ttvnw.net", f"{tag}.ttvnw.net"))
        if host == "ad.ttvnw.net":
            return httpx.Response(200, text=AD_MEDIA)
        if host == "clean.ttvnw.net":
            return httpx.Response(200, text=LIVE_MEDIA)
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), hits


def _resolve(client, proxies, **kwargs) -> tuple[str, int | None]:
    resolved = asyncio.run(twitch.resolve_live_playlist(client, "foo", proxies, **kwargs))
    assert resolved is not None
    return resolved


def test_has_ads():
    assert twitch.has_ads(AD_MEDIA)
    assert not twitch.has_ads(LIVE_MEDIA)


def test_resolve_redraws_past_a_preroll():
    client, hits = _proxy_world({"p1.example": [True, False]})
    url, height = _resolve(client, ["https://p1.example"])
    assert url.startswith("https://p1.example/live/foo") and height == 1080
    # master, probe of the lightest variant (160p), redraw, probe
    assert hits == [
        "p1.example/live/foo",
        "ad.ttvnw.net/160.m3u8",
        "p1.example/live/foo",
        "clean.ttvnw.net/160.m3u8",
    ]


def test_resolve_skips_a_proxy_that_keeps_serving_prerolls():
    client, _ = _proxy_world({"p1.example": [True], "p2.example": [False]})
    url, _h = _resolve(client, ["https://p1.example", "https://p2.example"], max_height=720)
    # capped: the handed-over variant belongs to the clean token
    assert url == "https://clean.ttvnw.net/720.m3u8"


def test_resolve_prefers_a_preroll_proxy_over_the_direct_stream():
    client, hits = _proxy_world({"p1.example": [True], "p2.example": [True]})
    url, _h = _resolve(client, ["https://p1.example", "https://p2.example"])
    assert url.startswith("https://p1.example/live/foo")
    assert hits.count("p1.example/live/foo") == twitch.AD_FREE_ATTEMPTS
    assert hits.count("p2.example/live/foo") == twitch.AD_FREE_ATTEMPTS


def test_probe_never_leaves_twitch_cdn():
    """The master comes from a third party: its variant URLs must not steer
    server-side fetches off Twitch's CDN."""
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url.host))
        return httpx.Response(
            200, text=MASTER.replace("https://cdn.ttvnw.net", "http://127.0.0.1:8776")
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    master, clean = asyncio.run(twitch.fetch_ad_free_master(client, "https://p1.example/live/foo"))
    assert master and clean
    assert hits == ["p1.example"]


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
