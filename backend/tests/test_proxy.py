"""Byte/HLS proxy endpoints and m3u8 URI rewriting."""

from __future__ import annotations

import urllib.parse

import httpx
import pytest

from ytui_server.routers.proxy import rewrite_hls


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
