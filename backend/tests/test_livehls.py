"""Self-healing live-HLS proxy: master/media rewriting and 403 recovery."""

from __future__ import annotations

import httpx
import pytest

from ytui_server.models import StreamInfo
from ytui_server.services import livehls

VIDEO = "abc123def45"
WATCH = f"https://www.youtube.com/watch?v={VIDEO}"
BASE_A = "https://rr1.googlevideo.com/api/manifest/hls_playlist/expire/1/a"
BASE_B = "https://rr2.googlevideo.com/api/manifest/hls_playlist/expire/2/b"


def _media(base: str, itag: int, first_sq: int, count: int = 3) -> str:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:5",
        f"#EXT-X-MEDIA-SEQUENCE:{first_sq}",
    ]
    for sq in range(first_sq, first_sq + count):
        lines.append("#EXTINF:5.000,")
        lines.append(f"{base}/itag/{itag}/index.m3u8/sq/{sq}/dur/5.000/file/seg.ts")
    return "\n".join(lines) + "\n"


def _master(base: str) -> str:
    return (
        "#EXTM3U\n"
        f'#EXT-X-MEDIA:URI="{base}/itag/233/index.m3u8",TYPE=AUDIO,'
        'GROUP-ID="233",NAME="Default",DEFAULT=YES\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=300000,RESOLUTION=426x240,AUDIO="233"\n'
        f"{base}/itag/229/index.m3u8\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720,AUDIO="233"\n'
        f"{base}/itag/232/index.m3u8\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080,AUDIO="233"\n'
        f"{base}/itag/270/index.m3u8\n"
    )


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
        return StreamInfo(kind="hls", url=f"{base}/master.m3u8", is_live=True)

    monkeypatch.setattr(livehls.ytdlp, "resolve_streams", fake_resolve)
    monkeypatch.setattr(livehls.ytdlp, "evict_cached_info", lambda url: None)
    return calls


def _transport(dead_bases: frozenset[str] = frozenset()):
    """Upstream: masters, media playlists and segments on both generations."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        base = BASE_A if "rr1" in request.url.host else BASE_B
        if path.endswith("master.m3u8"):
            return httpx.Response(200, text=_master(base))
        if "/sq/" in path:
            if base in dead_bases:
                return httpx.Response(403)
            return httpx.Response(200, content=b"tsbytes")
        if "/itag/" in path:  # media playlist
            itag = int(path.split("/itag/")[1].split("/")[0])
            return httpx.Response(200, text=_media(base, itag, 50))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_master_rewritten_with_cap(resolved):
    client = httpx.AsyncClient(transport=_transport())
    sess = livehls.get_session(WATCH, 720)
    text = await sess.entry_playlist(client)
    assert "media/229.m3u8" in text and "media/232.m3u8" in text
    assert "270" not in text  # above the cap: dropped
    assert 'URI="media/233.m3u8"' in text  # audio rendition kept and rewritten
    assert "googlevideo" not in text
    assert sess.media_urls[232].startswith(BASE_A)


async def test_master_keeps_lowest_when_nothing_fits(resolved):
    client = httpx.AsyncClient(transport=_transport())
    sess = livehls.get_session(WATCH, 144)
    text = await sess.entry_playlist(client)
    assert "media/229.m3u8" in text
    assert "media/232.m3u8" not in text and "media/270.m3u8" not in text


async def test_media_playlist_rewrites_segments(resolved):
    client = httpx.AsyncClient(transport=_transport())
    sess = livehls.get_session(WATCH, 720)
    await sess.entry_playlist(client)
    text = await sess.media_playlist(client, 232)
    assert "232/50" in text and "232/52" in text
    assert "googlevideo" not in text
    assert "/sq/51/" in sess.segments[(232, 51)]
    # Reading the playlist again refetches upstream without re-extracting.
    await sess.media_playlist(client, 232)
    assert resolved["n"] == 1


async def test_segment_reresolves_on_403(resolved):
    client = httpx.AsyncClient(transport=_transport(dead_bases={BASE_A}))
    sess = livehls.get_session(WATCH, 720)
    await sess.entry_playlist(client)
    await sess.media_playlist(client, 232)

    url = await sess.segment_upstream(client, 232, 51)
    assert url is not None and "rr1" in url
    assert (await client.get(url)).status_code == 403
    # What the router does on 403: re-resolve, then remap the same itag+sq.
    await sess._reresolve(client, sess.generation)
    await sess.media_playlist(client, 232)
    url2 = await sess.segment_upstream(client, 232, 51)
    assert url2 is not None and "rr2" in url2 and "/sq/51/" in url2
    assert (await client.get(url2)).content == b"tsbytes"
    assert resolved["n"] == 2


async def test_reresolve_dedupes_by_generation(resolved):
    client = httpx.AsyncClient(transport=_transport())
    sess = livehls.get_session(WATCH, 720)
    await sess.entry_playlist(client)
    gen = sess.generation
    await sess._reresolve(client, gen)
    # A caller holding the old generation does not trigger a second extraction.
    await sess._reresolve(client, gen)
    assert resolved["n"] == 2


async def test_segment_synthesized_for_old_sq(resolved):
    client = httpx.AsyncClient(transport=_transport())
    sess = livehls.get_session(WATCH, 720)
    await sess.entry_playlist(client)
    await sess.media_playlist(client, 232)
    url = await sess.segment_upstream(client, 232, 40)  # older than the window
    assert url is not None and "/sq/40/" in url and "/itag/232/" in url


async def test_single_media_live_serves_directly(resolved, monkeypatch):
    """A muxed (pre-2026.08) live: the entry is the media playlist itself."""

    async def fake_resolve(url, max_height=1440, **kwargs):
        return StreamInfo(kind="hls", url=f"{BASE_A}/itag/95/index.m3u8", is_live=True)

    monkeypatch.setattr(livehls.ytdlp, "resolve_streams", fake_resolve)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_media(BASE_A, 95, 60))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sess = livehls.get_session(WATCH, 720)
    text = await sess.entry_playlist(client)
    assert "95/60" in text
    assert (95, 61) in sess.segments


def test_get_session_keeps_cap_for_segment_requests():
    sess = livehls.get_session(WATCH, 720)
    assert livehls.get_session(WATCH) is sess
    assert sess.max_height == 720
    # An explicit new cap resets the session's resolution.
    sess.media_urls = {232: "https://x"}
    livehls.get_session(WATCH, 1080)
    assert sess.max_height == 1080
    assert sess.media_urls == {}
