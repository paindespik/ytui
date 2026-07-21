"""sidx parsing, MPD generation and the /api/videos/{id}/mpd route."""

from __future__ import annotations

import struct
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

from ytui_server.services import dash, ytdlp
from ytui_server.services.ytdlp import SplitMp4

NS = "{urn:mpeg:dash:schema:mpd:2011}"


def _box(fourcc: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + fourcc + payload


def _large_box(fourcc: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 1) + fourcc + struct.pack(">Q", 16 + len(payload)) + payload


def _split(video_url="https://v.example/video.mp4", audio_url="https://a.example/audio.m4a"):
    return SplitMp4(
        video_url=video_url,
        width=1920,
        height=1080,
        video_codec="avc1.640028",
        video_bitrate=4_000_000,
        audio_url=audio_url,
        audio_codec="mp4a.40.2",
        audio_bitrate=128_000,
        duration=212,
    )


# ─── find_sidx ───


def test_find_sidx_nominal():
    data = _box(b"ftyp", b"\x00" * 16) + _box(b"moov", b"\x00" * 32) + _box(b"sidx", b"\x00" * 40)
    assert dash.find_sidx(data) == (24 + 40, 48)


def test_find_sidx_largesize():
    data = _large_box(b"ftyp", b"\x00" * 16) + _box(b"sidx", b"\x00" * 8)
    assert dash.find_sidx(data) == (32, 16)


def test_find_sidx_absent():
    data = _box(b"ftyp", b"\x00" * 16) + _box(b"moov", b"\x00" * 32)
    assert dash.find_sidx(data) is None


def test_find_sidx_truncated():
    # Declared size runs past the available bytes; walker must stop cleanly.
    truncated = struct.pack(">I", 4096) + b"moov" + b"\x00" * 8
    assert dash.find_sidx(truncated) is None
    assert dash.find_sidx(b"\x00\x00\x01") is None
    # Zero size (box-to-EOF) and sizes below the header length stop the walk.
    assert dash.find_sidx(struct.pack(">I", 0) + b"sidx" + b"\x00" * 8) is None
    assert dash.find_sidx(struct.pack(">I", 1) + b"sidx") is None


# ─── build_mpd ───


def test_build_mpd_structure():
    split = _split(video_url="https://v.example/video.mp4?a=1&b=2")
    mpd = dash.build_mpd(split, lambda u: u, ("0-761", "762-1273"), ("0-661", "662-1001"))
    assert mpd.startswith("<?xml")
    root = ET.fromstring(mpd)
    assert root.tag == f"{NS}MPD"
    assert root.get("type") == "static"
    assert root.get("mediaPresentationDuration") == "PT212S"
    assert root.get("profiles") == "urn:mpeg:dash:profile:isoff-on-demand:2011"

    reps = {r.get("id"): r for r in root.iter(f"{NS}Representation")}
    video, audio = reps["video"], reps["audio"]
    assert video.get("codecs") == "avc1.640028"
    assert video.get("bandwidth") == "4000000"
    assert (video.get("width"), video.get("height")) == ("1920", "1080")
    assert audio.get("codecs") == "mp4a.40.2"
    assert audio.get("bandwidth") == "128000"

    # The '&' in the URL must round-trip through XML escaping.
    assert video.find(f"{NS}BaseURL").text == "https://v.example/video.mp4?a=1&b=2"
    seg = video.find(f"{NS}SegmentBase")
    assert seg.get("indexRange") == "762-1273"
    assert seg.find(f"{NS}Initialization").get("range") == "0-761"
    assert audio.find(f"{NS}SegmentBase").get("indexRange") == "662-1001"


# ─── /api/videos/{id}/mpd ───

_SIDX_BODY = _box(b"ftyp", b"\x00" * 16) + _box(b"moov", b"\x00" * 24) + _box(b"sidx", b"\x00" * 32)


def _patch_resolve(monkeypatch, result):
    async def fake_resolve(url, max_height):
        return result

    # videos.py resolves the attribute on the ytdlp module at call time.
    monkeypatch.setattr(ytdlp, "resolve_split_mp4", fake_resolve)


def _mock_proxy_client(app, status_code=206, content=_SIDX_BODY):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=content)

    app.state.proxy_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_mpd_route_ok(app, client, monkeypatch):
    split = _split()
    _patch_resolve(monkeypatch, split)
    _mock_proxy_client(app)
    resp = client.get("/api/videos/vid000000001/mpd")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/dash+xml")
    assert resp.headers["cache-control"] == "no-store"
    root = ET.fromstring(resp.text)
    urls = [b.text for b in root.iter(f"{NS}BaseURL")]
    assert len(urls) == 2
    prefix = "/api/proxy?url="
    assert all(u.startswith(prefix) for u in urls)
    assert urllib.parse.unquote(urls[0][len(prefix) :]) == split.video_url
    assert urllib.parse.unquote(urls[1][len(prefix) :]) == split.audio_url


def test_mpd_route_no_split_formats(app, client, monkeypatch):
    _patch_resolve(monkeypatch, None)
    _mock_proxy_client(app)
    resp = client.get("/api/videos/vid000000001/mpd")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No split MP4 formats"


def test_mpd_route_upstream_forbidden(app, client, monkeypatch):
    _patch_resolve(monkeypatch, _split())
    _mock_proxy_client(app, status_code=403, content=b"")
    resp = client.get("/api/videos/vid000000001/mpd")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "sidx not found"
