"""Public share links: creation, public page, scoped streams and signed relay."""

from __future__ import annotations

import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx
import pytest
from conftest import TEST_TOKEN, make_video

from ytui_server.models import StreamInfo, SubtitleTrackOut, Video
from ytui_server.services import share, tiktok, ytdlp
from ytui_server.services.ytdlp import SplitMp4

GV = "https://rr1---sn-test.googlevideo.com/videoplayback?id=1&itag=18"
SUBS = "https://www.youtube.com/api/timedtext?v=abc&lang=fr&fmt=vtt"


@pytest.fixture()
def resolved(monkeypatch):
    """Patch yt-dlp stream resolution; returns the list of calls made."""
    calls: list[tuple[str, int]] = []
    state = {"info": StreamInfo(kind="progressive", url=GV, title="Resolved title")}

    async def fake(url, max_height=1440, audio_only=False, sub_langs=""):
        calls.append((url, max_height))
        info = state["info"]
        return info(max_height) if callable(info) else info

    monkeypatch.setattr(ytdlp, "resolve_streams", fake)

    def set_info(info):
        state["info"] = info

    calls_api = type("Resolved", (), {"calls": calls, "set": staticmethod(set_info)})
    return calls_api


@pytest.fixture()
def upstream(app):
    """MockTransport behind the relay; returns the list of upstream URLs hit."""
    hits: list[str] = []
    bodies: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        for prefix, response in bodies.items():
            if str(request.url).startswith(prefix):
                return response
        return httpx.Response(200, content=b"media", headers={"Content-Type": "video/mp4"})

    app.state.proxy_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return type("Upstream", (), {"hits": hits, "bodies": bodies})


def _share(client, video: Video | None = None) -> dict:
    video = video or make_video("abc123def45", title="Shared clip")
    resp = client.post("/api/shares", json={"video": video.model_dump(mode="json")})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ─── management (authenticated) ───


def test_create_requires_auth(anon_client):
    video = make_video()
    resp = anon_client.post("/api/shares", json={"video": video.model_dump(mode="json")})
    assert resp.status_code == 401


def test_create_returns_page_path_and_seven_day_expiry(client):
    before = time.time()
    data = _share(client)
    assert data["path"] == f"/s/{data['token']}"
    assert len(data["token"]) >= 22
    assert data["video"]["video_id"] == "abc123def45"
    expires_at = datetime.fromisoformat(data["expires_at"]).timestamp()
    assert before + 7 * 86400 - 5 <= expires_at <= time.time() + 7 * 86400 + 5


def test_reshare_reuses_the_link_and_extends_it(client, app):
    first = _share(client)
    db = app.state.db
    db._conn.execute("UPDATE shares SET expires_at = ?", (time.time() + 60,))
    db._conn.commit()
    second = _share(client)
    assert second["token"] == first["token"]
    extended = datetime.fromisoformat(second["expires_at"]).timestamp()
    assert extended > time.time() + 6 * 86400


def test_expired_link_is_replaced_not_revived(client, app, anon_client):
    old = _share(client)
    db = app.state.db
    db._conn.execute("UPDATE shares SET expires_at = ?", (time.time() - 1,))
    db._conn.commit()
    assert anon_client.get(old["path"]).status_code == 404
    new = _share(client)
    assert new["token"] != old["token"]
    assert anon_client.get(old["path"]).status_code == 404
    assert anon_client.get(new["path"]).status_code == 200


def test_zero_ttl_never_expires(client, app):
    app.state.settings.share_ttl_days = 0
    data = _share(client)
    assert data["expires_at"] is None


def test_only_videos_can_be_shared(client):
    channel = make_video("UCabc", kind="channel")
    resp = client.post("/api/shares", json={"video": channel.model_dump(mode="json")})
    assert resp.status_code == 400


def test_list_and_revoke(client, anon_client, resolved):
    data = _share(client)
    listed = client.get("/api/shares").json()
    assert [s["token"] for s in listed] == [data["token"]]
    assert client.delete(f"/api/shares/{data['token']}").status_code == 204
    assert client.get("/api/shares").json() == []
    assert anon_client.get(data["path"]).status_code == 404
    assert anon_client.get(f"{data['path']}/streams").status_code == 404
    assert client.delete(f"/api/shares/{data['token']}").status_code == 404


# ─── public page ───


def test_page_is_public_escaped_and_locked_down(client, anon_client):
    hostile = make_video(
        "abc123def45",
        title='<script>alert(1)</script>"><img src=x>',
        channel_title="Chan & Co",
    )
    data = _share(client, hostile)
    resp = anon_client.get(data["path"])
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "Chan &amp; Co" in body
    assert 'property="og:title"' in body
    assert "https://i.ytimg.com/vi/abc123def45/hqdefault.jpg" in body
    csp = resp.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in resp.headers["x-robots-tag"]
    # The page itself resolves nothing upstream (link-preview bots).
    assert 'src="/js/share.js"' in body


def test_page_rejects_non_https_thumbnails(client, anon_client):
    data = _share(
        client,
        make_video("x1", platform="bitchute", thumbnail_url="javascript:alert(1)"),
    )
    body = anon_client.get(data["path"]).text
    assert "javascript:" not in body
    assert "og:image" not in body


def test_unknown_link_page(anon_client):
    resp = anon_client.get("/s/nope")
    assert resp.status_code == 404
    assert "plus valable" in resp.text


def test_share_token_opens_no_api_route(client, anon_client):
    data = _share(client)
    for headers in ({"Authorization": f"Bearer {data['token']}"}, {}):
        assert anon_client.get("/api/feed", headers=headers).status_code == 401
    anon_client.cookies.set("ytui_session", data["token"])
    assert anon_client.get("/api/history").status_code == 401


# ─── streams ───


def _query(url: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def _assert_signed(url: str, token: str, endpoint: str, upstream_url: str) -> None:
    assert url.startswith(f"/s/{token}/{endpoint}?")
    q = _query(url)
    assert q["u"] == upstream_url
    assert share.verify(TEST_TOKEN, token, q["u"], int(q["e"]), q["s"])


def test_streams_youtube_vod_dash_then_signed_progressive(client, anon_client, resolved):
    resolved.set(
        StreamInfo(
            kind="progressive",
            url=GV,
            title="Resolved title",
            duration=212,
            subtitles=[SubtitleTrackOut(lang="fr", label="Français", url=SUBS)],
        )
    )
    data = _share(client)
    token = data["token"]
    resp = anon_client.get(f"{data['path']}/streams", params={"max_height": 720})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Resolved title"
    assert body["is_live"] is False
    sources = body["sources"]
    assert [s["engine"] for s in sources] == ["dash", "progressive"]
    assert sources[0]["url"] == f"/s/{token}/mpd?max_height=720"
    _assert_signed(sources[1]["url"], token, "p", GV)
    _assert_signed(body["subtitles"][0]["url"], token, "p", SUBS)
    # The share's own video, whatever the caller would like.
    assert resolved.calls == [("https://www.youtube.com/watch?v=abc123def45", 720)]


def test_streams_default_cap_is_1080(client, anon_client, resolved):
    data = _share(client)
    anon_client.get(f"{data['path']}/streams")
    assert resolved.calls[0][1] == 1080


def test_streams_split_degrades_to_360_progressive(client, anon_client, resolved):
    low = StreamInfo(kind="progressive", url=GV)
    high = StreamInfo(kind="split", url="", video_url=GV, audio_url=GV)
    resolved.set(lambda h: low if h == 360 else high)
    data = _share(client)
    sources = anon_client.get(f"{data['path']}/streams").json()["sources"]
    assert [s["engine"] for s in sources] == ["dash", "progressive"]


def test_streams_hls_playlist_is_signed(client, anon_client, resolved):
    data = _share(client, make_video("v123", platform="twitch"))
    m3u8 = "https://usher.ttvnw.net/vod/123.m3u8?sig=1"
    resolved.set(StreamInfo(kind="hls", url=m3u8))
    sources = anon_client.get(f"{data['path']}/streams").json()["sources"]
    assert [s["engine"] for s in sources] == ["hls"]
    _assert_signed(sources[0]["url"], data["token"], "hls", m3u8)


def test_streams_youtube_live_uses_share_scoped_live_proxy(client, anon_client, resolved):
    resolved.set(
        StreamInfo(
            kind="hls",
            url="https://manifest.googlevideo.com/x.m3u8",
            is_live=True,
            proxy_path="/api/live/abc123def45/playlist.m3u8?max_height=1080",
        )
    )
    data = _share(client)
    body = anon_client.get(f"{data['path']}/streams").json()
    assert body["is_live"] is True
    assert body["sources"] == [
        {"engine": "hls", "url": f"/s/{data['token']}/live/playlist.m3u8?max_height=1080"}
    ]


def test_live_routes_are_youtube_only(client, anon_client):
    data = _share(client, make_video("x1", platform="bitchute"))
    assert anon_client.get(f"{data['path']}/live/playlist.m3u8").status_code == 404


# ─── Twitch / TikTok lives: pinned to the stream on air ───


def _live(video_id: str, platform: str = "twitch") -> Video:
    return Video(
        video_id=video_id,
        title="On air",
        channel_title="Streamer",
        platform=platform,
        thumbnail_url="https://static-cdn.jtvnw.net/x.jpg",
    )


def test_channel_live_share_pins_the_current_stream(client, anon_client, monkeypatch, resolved):
    current: dict[str, Video | None] = {"live": _live("streamer:111")}

    async def fake_current(platform, login):
        assert (platform, login) == ("twitch", "streamer")
        return current["live"]

    monkeypatch.setattr(share, "current_live", fake_current)
    # The client may only know the channel ("login:0"): the share pins the
    # stream actually on air.
    data = _share(client, make_video("streamer:0", platform="twitch", title=""))
    assert data["video"]["video_id"] == "streamer:111"
    assert data["video"]["title"] == "On air"

    resolved.set(StreamInfo(kind="hls", url="https://usher.ttvnw.net/live.m3u8"))
    # No ad-free playlist proxies: straight to the (mocked) yt-dlp resolution.
    client.app.state.settings.twitch_proxies = ""
    resp = anon_client.get(f"{data['path']}/streams")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_live"] is True

    # The channel's NEXT live must not play through this link.
    current["live"] = _live("streamer:222")
    assert anon_client.get(f"{data['path']}/streams").status_code == 410
    current["live"] = None
    assert anon_client.get(f"{data['path']}/streams").status_code == 410


def test_signed_twitch_master_cannot_open_the_next_live(
    client, anon_client, monkeypatch, resolved, upstream
):
    current: dict[str, Video | None] = {"live": _live("streamer:111")}

    async def fake_current(platform, login):
        return current["live"]

    monkeypatch.setattr(share, "current_live", fake_current)
    client.app.state.settings.twitch_proxies = ""
    master = "https://usher.ttvnw.net/api/channel/hls/streamer.m3u8?token=t&sig=s"
    resolved.set(StreamInfo(kind="hls", url=master))
    upstream.bodies["https://usher.ttvnw.net/"] = httpx.Response(
        200, text="#EXTM3U\nhttps://video-weaver.fra05.hls.ttvnw.net/v1/playlist/x.m3u8\n"
    )
    data = _share(client, make_video("streamer:0", platform="twitch"))
    url = anon_client.get(f"{data['path']}/streams").json()["sources"][0]["url"]
    assert anon_client.get(url).status_code == 200
    # Hours later, same signed URL, the channel streams something else.
    current["live"] = _live("streamer:222")
    assert anon_client.get(url).status_code == 410
    assert anon_client.get(f"{data['path']}/mpd").status_code == 404


def test_tiktok_flv_live_is_relayed_as_signed_bytes(client, anon_client, monkeypatch):
    async def fake_current(platform, login):
        return _live("someone:7", platform="tiktok")

    flv = "https://pull-flv-l1.tiktokcdn.com/stage/stream-7.flv?expire=1"

    async def fake_resolve(client_, username, max_height):
        assert username == "someone"
        return flv, "On air", 720

    monkeypatch.setattr(share, "current_live", fake_current)
    monkeypatch.setattr(tiktok, "resolve_live_stream", fake_resolve)
    data = _share(client, make_video("someone:7", platform="tiktok"))
    body = anon_client.get(f"{data['path']}/streams").json()
    assert body["is_live"] is True
    assert [s["engine"] for s in body["sources"]] == ["flv"]
    _assert_signed(body["sources"][0]["url"], data["token"], "p", flv)


def test_channel_live_share_refused_when_offline(client, monkeypatch):
    async def offline(platform, login):
        return None

    monkeypatch.setattr(share, "current_live", offline)
    video = make_video("someone:5", platform="tiktok")
    resp = client.post("/api/shares", json={"video": video.model_dump(mode="json")})
    assert resp.status_code == 409


# ─── signed relay ───


def test_relay_serves_signed_urls_only(client, anon_client, resolved, upstream):
    data = _share(client)
    token = data["token"]
    url = anon_client.get(f"{data['path']}/streams").json()["sources"][1]["url"]
    resp = anon_client.get(url, headers={"Range": "bytes=0-3"})
    assert resp.status_code == 200
    assert resp.content == b"media"
    assert upstream.hits == [GV]

    q = _query(url)
    other = "https://rr1---sn-test.googlevideo.com/videoplayback?id=OTHER"
    # Another URL under the same signature.
    assert anon_client.get(f"/s/{token}/p", params={**q, "u": other}).status_code == 403
    # Unsigned / garbled signature.
    assert anon_client.get(f"/s/{token}/p", params={"u": other, "e": q["e"], "s": "x"}).status_code == 403
    assert anon_client.get(f"/s/{token}/p", params={"u": other}).status_code == 422
    # Expired signature.
    past = int(time.time()) - 10
    stale = share.sign(TEST_TOKEN, token, GV, past)
    assert anon_client.get(f"/s/{token}/p", params={"u": GV, "e": past, "s": stale}).status_code == 403
    assert upstream.hits == [GV]


def test_signature_is_bound_to_its_share(client, anon_client, resolved, upstream):
    first = _share(client)
    second = _share(client, make_video("zzz999zzz99", title="Other"))
    url = anon_client.get(f"{first['path']}/streams").json()["sources"][1]["url"]
    q = _query(url)
    assert anon_client.get(f"/s/{second['token']}/p", params=q).status_code == 403
    assert anon_client.get(f"/s/{first['token']}/p", params=q).status_code == 200


def test_relay_dies_with_the_share(client, anon_client, resolved, upstream):
    data = _share(client)
    url = anon_client.get(f"{data['path']}/streams").json()["sources"][1]["url"]
    client.delete(f"/api/shares/{data['token']}")
    assert anon_client.get(url).status_code == 404
    assert upstream.hits == []


def test_relay_keeps_the_proxy_allowlist(client, anon_client):
    data = _share(client)
    evil = "http://169.254.169.254/latest/meta-data"
    exp = int(time.time()) + 60
    sig = share.sign(TEST_TOKEN, data["token"], evil, exp)
    resp = anon_client.get(f"/s/{data['token']}/p", params={"u": evil, "e": exp, "s": sig})
    assert resp.status_code == 403


def test_relayed_playlists_are_re_signed(client, anon_client, resolved, upstream):
    data = _share(client, make_video("v123", platform="twitch"))
    token = data["token"]
    master = "https://usher.ttvnw.net/vod/123.m3u8"
    resolved.set(StreamInfo(kind="hls", url=master))
    upstream.bodies[master] = httpx.Response(
        200,
        text="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nchunked/index.m3u8\n"
        '#EXT-X-MAP:URI="init.mp4"\nseg1.ts\n',
        headers={"Content-Type": "application/vnd.apple.mpegurl"},
    )
    url = anon_client.get(f"{data['path']}/streams").json()["sources"][0]["url"]
    resp = anon_client.get(url)
    assert resp.status_code == 200
    lines = [line for line in resp.text.splitlines() if line and not line.startswith("#")]
    _assert_signed(lines[0], token, "hls", "https://usher.ttvnw.net/vod/chunked/index.m3u8")
    _assert_signed(lines[1], token, "p", "https://usher.ttvnw.net/vod/seg1.ts")
    map_uri = resp.text.split('URI="', 1)[1].split('"', 1)[0]
    _assert_signed(map_uri, token, "p", "https://usher.ttvnw.net/vod/init.mp4")


# ─── DASH manifest & SponsorBlock ───


def test_mpd_media_urls_are_signed(client, anon_client, monkeypatch, app):
    split = SplitMp4(
        video_url="https://rr1---sn-test.googlevideo.com/v?itag=137",
        width=1920,
        height=1080,
        video_codec="avc1.640028",
        video_bitrate=4_000_000,
        audio_url="https://rr1---sn-test.googlevideo.com/v?itag=140",
        audio_codec="mp4a.40.2",
        audio_bitrate=128_000,
        duration=212,
    )

    async def fake_split(url, max_height):
        assert url == "https://www.youtube.com/watch?v=abc123def45"
        return split

    async def fake_probe(client_, url):
        return "0-99", "100-199"

    from ytui_server.services import dash

    monkeypatch.setattr(ytdlp, "resolve_split_mp4", fake_split)
    monkeypatch.setattr(dash, "probe_ranges", fake_probe)
    data = _share(client)
    resp = anon_client.get(f"{data['path']}/mpd")
    assert resp.status_code == 200
    ns = "{urn:mpeg:dash:schema:mpd:2011}"
    urls = [b.text for b in ET.fromstring(resp.text).iter(f"{ns}BaseURL")]
    _assert_signed(urls[0] or "", data["token"], "p", split.video_url)
    _assert_signed(urls[1] or "", data["token"], "p", split.audio_url)


def test_sponsor_segments(client, anon_client, app, monkeypatch):
    async def fake_get(video_id):
        assert video_id == "abc123def45"
        return [{"category": "sponsor", "start": 10.0, "end": 20.0}]

    monkeypatch.setattr(app.state.sponsorblock, "get", fake_get)
    data = _share(client)
    body = anon_client.get(f"{data['path']}/sponsor").json()
    assert body["segments"] == [{"category": "sponsor", "start": 10.0, "end": 20.0}]
    other = _share(client, make_video("x1", platform="bitchute"))
    assert anon_client.get(f"{other['path']}/sponsor").json() == {"segments": []}
