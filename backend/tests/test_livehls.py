"""Self-healing live-HLS proxy: playlist rewriting and 403 recovery."""

from __future__ import annotations

import httpx
import pytest

from ytui_server.models import StreamInfo
from ytui_server.services import livehls

VIDEO = "abc123def45"
WATCH = f"https://www.youtube.com/watch?v={VIDEO}"
BASE_A = "https://rr1.googlevideo.com/videoplayback/x/hls_playlist/expire/1/a"
BASE_B = "https://rr2.googlevideo.com/videoplayback/x/hls_playlist/expire/2/b"


def _playlist(base: str, first_sq: int, count: int = 3) -> str:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:5",
        f"#EXT-X-MEDIA-SEQUENCE:{first_sq}",
    ]
    for sq in range(first_sq, first_sq + count):
        lines.append("#EXTINF:5.000,")
        lines.append(f"{base}/playlist/index.m3u8/sq/{sq}/dur/5.000/file/seg.ts")
    return "\n".join(lines) + "\n"


MASTER = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=300000,RESOLUTION=426x240\n"
    f"{BASE_A}/itag/91/index.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720\n"
    f"{BASE_A}/itag/95/index.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080\n"
    f"{BASE_A}/itag/96/index.m3u8\n"
)


def test_pick_variant_respects_cap():
    assert livehls._pick_variant(MASTER, 720) == f"{BASE_A}/itag/95/index.m3u8"
    assert livehls._pick_variant(MASTER, 2160) == f"{BASE_A}/itag/96/index.m3u8"
    # Nothing fits: the lowest available still plays.
    assert livehls._pick_variant(MASTER, 144) == f"{BASE_A}/itag/91/index.m3u8"


def test_ingest_rewrites_segments_and_maps_sq():
    sess = livehls.LiveHlsSession(WATCH, 1080)
    sess._ingest(_playlist(BASE_A, 100))
    assert "seg/100" in sess.playlist_text
    assert "googlevideo" not in sess.playlist_text
    assert sess.segments[101].startswith(BASE_A)
    assert "/sq/101/" in sess.segments[101]


@pytest.fixture(autouse=True)
def _fresh_sessions():
    livehls._sessions.clear()
    yield
    livehls._sessions.clear()


@pytest.fixture()
def resolved(monkeypatch):
    """Stub extraction: each resolve serves a new base URL generation."""
    bases = [BASE_A, BASE_B]
    calls = {"n": 0}

    async def fake_resolve(url, max_height=1440, **kwargs):
        base = bases[min(calls["n"], len(bases) - 1)]
        calls["n"] += 1
        return StreamInfo(kind="hls", url=f"{base}/itag/95/index.m3u8", is_live=True)

    monkeypatch.setattr(livehls.ytdlp, "resolve_streams", fake_resolve)
    monkeypatch.setattr(livehls.ytdlp, "evict_cached_info", lambda url: None)
    return calls


async def test_playlist_resolves_and_serves(resolved):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("index.m3u8"):
            return httpx.Response(200, text=_playlist(BASE_A, 50))
        return httpx.Response(200, content=b"tsbytes")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sess = livehls.get_session(WATCH, 1080)
    text = await sess.playlist(client)
    assert "seg/50" in text and "seg/52" in text
    assert resolved["n"] == 1
    # A second playlist read refetches upstream without re-extracting.
    await sess.playlist(client)
    assert resolved["n"] == 1


async def test_segment_reresolves_on_403(resolved):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("index.m3u8"):
            base = BASE_A if "rr1" in request.url.host else BASE_B
            return httpx.Response(200, text=_playlist(base, 50))
        if "rr1" in request.url.host:
            return httpx.Response(403)  # generation A died
        return httpx.Response(200, content=b"fresh")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sess = livehls.get_session(WATCH, 1080)
    await sess.playlist(client)

    url = await sess.segment_upstream(client, 51)
    assert url is not None and "rr1" in url
    resp = await client.get(url)
    assert resp.status_code == 403
    # What the router does on 403: re-resolve, then remap the same sq.
    await sess._reresolve(client, sess.generation)
    url2 = await sess.segment_upstream(client, 51)
    assert url2 is not None and "rr2" in url2 and "/sq/51/" in url2
    assert (await client.get(url2)).content == b"fresh"
    assert resolved["n"] == 2


async def test_reresolve_dedupes_by_generation(resolved):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_playlist(BASE_B, 60))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sess = livehls.get_session(WATCH, 1080)
    await sess.playlist(client)
    gen = sess.generation
    await sess._reresolve(client, gen)
    # A caller holding the old generation does not trigger a second extraction.
    await sess._reresolve(client, gen)
    assert resolved["n"] == 2


async def test_segment_synthesized_for_old_sq(resolved):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_playlist(BASE_A, 50))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sess = livehls.get_session(WATCH, 1080)
    await sess.playlist(client)
    url = await sess.segment_upstream(client, 40)  # older than the window
    assert url is not None and "/sq/40/" in url


def test_get_session_keeps_cap_for_segment_requests():
    sess = livehls.get_session(WATCH, 720)
    assert livehls.get_session(WATCH) is sess
    assert sess.max_height == 720
    # An explicit new cap resets the session's resolution.
    sess.playlist_url = "https://x"
    livehls.get_session(WATCH, 1080)
    assert sess.max_height == 1080
    assert sess.playlist_url is None
