"""Byte/HLS proxy endpoints and m3u8 URI rewriting."""

from __future__ import annotations

import urllib.parse

import httpx
import pytest
from conftest import TEST_TOKEN

from ytui_server.routers.proxy import rewrite_hls
from ytui_server.settings import Settings


@pytest.fixture()
def settings(tmp_path):
    """Test CDN hosts ride the operator-extendable proxy allowlist."""
    return Settings(
        YTUI_API_TOKEN=TEST_TOKEN,
        YTUI_DATA_DIR=tmp_path / "data",
        YTUI_PROXY_ALLOWED_DOMAINS="cdn.example,other.example",
    )


@pytest.fixture()
def proxied(client, app):
    """Install a MockTransport upstream and return the authenticated client."""

    def install(handler):
        app.state.proxy_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        return client

    return install


def test_proxy_passthrough(proxied):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(
            200, content=b"media-bytes", headers={"Content-Type": "video/mp4"}
        )

    client = proxied(handler)
    resp = client.get("/api/proxy", params={"url": "https://cdn.example/seg.mp4"})
    assert resp.status_code == 200
    assert resp.content == b"media-bytes"
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["x-accel-buffering"] == "no"


def test_proxy_forwards_range(proxied):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Range"] == "bytes=10-19"
        return httpx.Response(
            206,
            content=b"0123456789",
            headers={
                "Content-Type": "video/mp4",
                "Content-Range": "bytes 10-19/100",
                "Accept-Ranges": "bytes",
            },
        )

    client = proxied(handler)
    resp = client.get(
        "/api/proxy",
        params={"url": "https://cdn.example/seg.mp4"},
        headers={"Range": "bytes=10-19"},
    )
    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 10-19/100"
    assert resp.headers["accept-ranges"] == "bytes"


def test_proxy_rejects_non_http(client):
    resp = client.get("/api/proxy", params={"url": "file:///etc/passwd"})
    assert resp.status_code == 400


def test_proxy_upstream_error(proxied):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = proxied(handler)
    resp = client.get("/api/proxy", params={"url": "https://cdn.example/seg.mp4"})
    assert resp.status_code == 502


def test_proxy_requires_auth(anon_client):
    resp = anon_client.get("/api/proxy", params={"url": "https://cdn.example/x"})
    assert resp.status_code == 401


def test_proxy_hls_endpoint_rewrites(proxied):
    playlist = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nlow/index.m3u8\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=playlist)

    client = proxied(handler)
    resp = client.get(
        "/api/proxy/hls", params={"url": "https://cdn.example/master.m3u8"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    encoded = urllib.parse.quote("https://cdn.example/low/index.m3u8", safe="")
    assert f"/api/proxy/hls?url={encoded}" in resp.text


def test_proxy_hls_upstream_error_status(proxied):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="denied")

    client = proxied(handler)
    resp = client.get(
        "/api/proxy/hls", params={"url": "https://cdn.example/master.m3u8"}
    )
    assert resp.status_code == 403


MASTER = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="fr",URI="https://cdn.example/audio/fr.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=628000,RESOLUTION=640x360
video/360p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2128000,RESOLUTION=1280x720
https://other.example/720p.m3u8?token=a&b=c
"""

MEDIA = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-KEY:METHOD=AES-128,URI="keys/k1.bin",IV=0x1234
#EXT-X-MAP:URI="init.mp4"
#EXTINF:6.0,
seg-001.ts
#EXTINF:6.0,
https://cdn.example/abs/seg-002.ts
#EXT-X-ENDLIST
"""


def _enc(url: str) -> str:
    return urllib.parse.quote(url, safe="")


def test_rewrite_hls_master():
    out = rewrite_hls(MASTER, "https://cdn.example/master.m3u8")
    lines = out.splitlines()
    assert lines[0] == "#EXTM3U"
    # EXT-X-MEDIA alternate rendition is itself a playlist.
    assert f'URI="/api/proxy/hls?url={_enc("https://cdn.example/audio/fr.m3u8")}"' in lines[1]
    # Relative variant resolved against the master URL.
    assert lines[3] == f"/api/proxy/hls?url={_enc('https://cdn.example/video/360p.m3u8')}"
    # Absolute variant on another host, query preserved inside the encoding.
    assert lines[5] == f"/api/proxy/hls?url={_enc('https://other.example/720p.m3u8?token=a&b=c')}"
    # STREAM-INF attribute lines untouched.
    assert lines[2].startswith("#EXT-X-STREAM-INF:BANDWIDTH=628000")


def test_rewrite_hls_media():
    out = rewrite_hls(MEDIA, "https://cdn.example/video/360p.m3u8")
    lines = out.splitlines()
    assert f'URI="/api/proxy?url={_enc("https://cdn.example/video/keys/k1.bin")}"' in lines[2]
    assert ",IV=0x1234" in lines[2]
    assert f'URI="/api/proxy?url={_enc("https://cdn.example/video/init.mp4")}"' in lines[3]
    assert lines[5] == f"/api/proxy?url={_enc('https://cdn.example/video/seg-001.ts')}"
    assert lines[7] == f"/api/proxy?url={_enc('https://cdn.example/abs/seg-002.ts')}"
    assert lines[4] == "#EXTINF:6.0,"
    assert lines[8] == "#EXT-X-ENDLIST"


# ─── destination allowlist ───


def test_proxy_rejects_unlisted_domain(client):
    resp = client.get("/api/proxy", params={"url": "https://evil.internal/x.mp4"})
    assert resp.status_code == 403
    resp = client.get("/api/proxy/hls", params={"url": "https://evil.internal/m.m3u8"})
    assert resp.status_code == 403


def test_proxy_allows_builtin_cdns(proxied):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x", headers={"Content-Type": "video/mp4"})

    client = proxied(handler)
    for url in (
        "https://rr3---sn-abc.googlevideo.com/videoplayback?x=1",
        "https://video-edge-abc.fra02.abs.hls.ttvnw.net/v1/seg.ts",
        "https://pull-flv-l1.tiktokcdn.com/live/x.flv",
        "https://player.odycdn.com/api/v4/streams/free/x/y/z.mp4",
        "https://seed122.bitchute.com/x/video.mp4",
        "https://vid.divulg.org/x/master.m3u8",
    ):
        assert client.get("/api/proxy", params={"url": url}).status_code == 200, url


def test_proxy_sends_a_referer_to_odycdn_only(proxied):
    """player.odycdn.com answers 401 to a GET without Referer."""
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.host] = request.headers.get("Referer")
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(200, text="#EXTM3U\nseg.ts\n")
        return httpx.Response(206, content=b"x", headers={"Content-Type": "video/mp4"})

    client = proxied(handler)
    client.get("/api/proxy", params={"url": "https://player.odycdn.com/v6/streams/a/b.mp4"})
    client.get("/api/proxy/hls", params={"url": "https://secure.odycdn.com/x/master.m3u8"})
    client.get("/api/proxy", params={"url": "https://rr3---sn-abc.googlevideo.com/v?x=1"})
    assert seen == {
        "player.odycdn.com": "https://odysee.com/",
        "secure.odycdn.com": "https://odysee.com/",
        "rr3---sn-abc.googlevideo.com": None,
    }


def test_proxy_allows_configured_twitch_proxies(proxied):
    """Hosts of YTUI_TWITCH_PROXIES pass without being hardcoded."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"m3u8")

    client = proxied(handler)
    resp = client.get(
        "/api/proxy", params={"url": "https://eu.luminous.dev/playlist/chan.m3u8"}
    )
    assert resp.status_code == 200


def test_proxy_hls_redraws_twitch_master_past_a_preroll(proxied):
    """The player's own master is a fresh token: one that opens on a stitched
    preroll is re-fetched before reaching hls.js."""
    hits: list[str] = []
    draws = ["ad", "ad", "clean"]

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.host)
        if request.url.host == "eu.luminous.dev":
            tag = draws.pop(0)
            return httpx.Response(
                200,
                text=(
                    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=230000,RESOLUTION=284x160\n"
                    f"https://{tag}.playlist.ttvnw.net/v1/playlist/160.m3u8\n"
                ),
            )
        if request.url.host == "ad.playlist.ttvnw.net":
            return httpx.Response(
                200,
                text='#EXTM3U\n#EXT-X-DATERANGE:ID="stitched-ad-1-30",CLASS="twitch-stitched-ad"\n',
            )
        return httpx.Response(200, text="#EXTM3U\n#EXTINF:2.0,live\nseg.ts\n")

    client = proxied(handler)
    resp = client.get(
        "/api/proxy/hls", params={"url": "https://eu.luminous.dev/live/foo?allow_source=true"}
    )
    assert resp.status_code == 200
    assert _enc("https://clean.playlist.ttvnw.net/v1/playlist/160.m3u8") in resp.text
    assert "ad.playlist" not in resp.text
    assert hits.count("eu.luminous.dev") == 3


def test_proxy_hls_twitch_master_proxy_down(proxied):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="{}")

    client = proxied(handler)
    resp = client.get("/api/proxy/hls", params={"url": "https://eu.luminous.dev/live/foo"})
    assert resp.status_code == 502


def test_proxy_env_extension(proxied):
    """cdn.example passes only because the fixture extends the allowlist."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x")

    client = proxied(handler)
    resp = client.get("/api/proxy", params={"url": "https://sub.cdn.example/seg.ts"})
    assert resp.status_code == 200


def test_proxy_upstream_403_evicts_dead_extraction(proxied):
    """Un 403 googlevideo vu en lecture purge l'extraction en cache : le retry
    du lecteur re-résout au lieu de recevoir les mêmes URLs mortes."""
    import time

    from ytui_server.services import ytdlp

    dead = "https://rr1---sn-x.googlevideo.com/videoplayback?expire=1&id=o-dead&itag=137"
    ytdlp._INFO_CACHE["https://www.youtube.com/watch?v=dead"] = (
        time.monotonic(),
        {"formats": [{"url": dead}]},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"")

    client = proxied(handler)
    resp = client.get("/api/proxy", params={"url": dead})

    assert resp.status_code == 403
    assert not ytdlp._INFO_CACHE


# ─── hardening: inert documents, redirect allowlist ───


def test_proxy_downgrades_active_content_types(proxied):
    """An HTML/SVG file on an allowlisted CDN must not render on our origin."""
    served = {"type": "text/html; charset=utf-8"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<script>x</script>", headers={"Content-Type": served["type"]})

    client = proxied(handler)
    for bad in ("text/html; charset=utf-8", "image/svg+xml", "application/xhtml+xml"):
        served["type"] = bad
        resp = client.get("/api/proxy", params={"url": "https://cdn.example/f"})
        assert resp.headers["content-type"] == "application/octet-stream", bad
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in resp.headers["content-security-policy"]
    for good in ("video/mp4", "text/vtt; charset=utf-8", "video/mp2t", "image/jpeg"):
        served["type"] = good
        resp = client.get("/api/proxy", params={"url": "https://cdn.example/f"})
        assert resp.headers["content-type"] == good


def test_proxy_follows_allowlisted_redirects(proxied):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cdn.example":
            return httpx.Response(302, headers={"Location": "https://other.example/real.mp4"})
        assert request.headers["Range"] == "bytes=0-9"  # Range survives the hop
        return httpx.Response(206, content=b"0123456789", headers={"Content-Type": "video/mp4"})

    client = proxied(handler)
    resp = client.get(
        "/api/proxy",
        params={"url": "https://cdn.example/x.mp4"},
        headers={"Range": "bytes=0-9"},
    )
    assert resp.status_code == 206
    assert resp.content == b"0123456789"


def test_proxy_refuses_redirect_off_the_allowlist(proxied):
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.host)
        if request.url.host == "cdn.example":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8776/api/status"})
        return httpx.Response(200, content=b"secret")

    client = proxied(handler)
    for path in ("/api/proxy", "/api/proxy/hls"):
        hits.clear()
        resp = client.get(path, params={"url": "https://cdn.example/x"})
        assert resp.status_code == 403, path
        assert hits == ["cdn.example"], "the off-list hop must never be requested"


def test_proxy_hls_rewrites_against_final_url(proxied):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start.m3u8":
            return httpx.Response(302, headers={"Location": "https://other.example/v/real.m3u8"})
        return httpx.Response(200, text="#EXTM3U\n#EXTINF:6.0,\nseg.ts\n")

    client = proxied(handler)
    resp = client.get("/api/proxy/hls", params={"url": "https://cdn.example/start.m3u8"})
    assert resp.status_code == 200
    assert _enc("https://other.example/v/seg.ts") in resp.text
