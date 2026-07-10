"""RSS parsing, handle resolution (mocked HTTP), and feed merge/sort tests."""

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from ytui.cache import MetaCache
from ytui.models import Video
from ytui.sources.rss import RSSSource, extract_channel_id, merge_feeds, parse_rss

FIXTURES = Path(__file__).parent / "fixtures"

_RealAsyncClient = httpx.AsyncClient


def _client(transport):
    return _RealAsyncClient(transport=transport)


@pytest.fixture
def cache(tmp_path):
    c = MetaCache(tmp_path / "meta.sqlite")
    yield c
    c.close()


# -- RSS parsing --


def test_parse_rss_fixture():
    videos = parse_rss(FIXTURES.joinpath("channel_feed.xml").read_bytes())
    assert len(videos) == 2
    first = videos[0]
    assert first.video_id == "abc123def45"
    assert first.title == "First Video Title"
    assert first.channel_title == "Test Channel"
    assert first.channel_id == "UCtest000000000000000000"
    assert first.published == datetime(2024, 6, 2, 12, 0, 0, tzinfo=timezone.utc)
    assert first.url == "https://www.youtube.com/watch?v=abc123def45"
    assert "abc123def45" in first.thumbnail_url


def test_parse_rss_empty():
    assert parse_rss("<feed xmlns='http://www.w3.org/2005/Atom'></feed>") == []


# -- handle resolution --

HANDLE_HTML = (
    "<html><head>"
    '<link rel="canonical" href="https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv">'
    "</head><body>"
    '{"channelId":"UCabcdefghijklmnopqrstuv"}'
    "</body></html>"
)


def test_extract_channel_id_canonical():
    assert extract_channel_id(HANDLE_HTML) == "UCabcdefghijklmnopqrstuv"


def test_extract_channel_id_json_only():
    html = 'stuff {"channelId":"UC0123456789_-abcdefghij"} stuff'
    assert extract_channel_id(html) == "UC0123456789_-abcdefghij"


def test_extract_channel_id_missing():
    assert extract_channel_id("<html>nope</html>") is None


async def test_resolve_handle_scrapes_and_caches(cache):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=HANDLE_HTML)

    source = RSSSource(["@SomeHandle"], cache)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        cid = await source.resolve_channel("@SomeHandle", client)
        assert cid == "UCabcdefghijklmnopqrstuv"
        # Second resolution must hit the cache, not the network.
        cid2 = await source.resolve_channel("@SomeHandle", client)
        assert cid2 == cid
    assert len(calls) == 1
    assert "youtube.com/@SomeHandle" in calls[0]
    assert cache.get_handle("@SomeHandle") == "UCabcdefghijklmnopqrstuv"


async def test_resolve_channel_id_passthrough(cache):
    source = RSSSource([], cache)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    ) as client:
        assert await source.resolve_channel("UCxyz", client) == "UCxyz"


# -- merge/sort --


def _video(vid: str, published: datetime | None) -> Video:
    return Video(video_id=vid, title=vid, published=published)


def test_merge_feeds_sorted_newest_first():
    d = lambda day: datetime(2024, 6, day, tzinfo=timezone.utc)  # noqa: E731
    feed_a = [_video("a2", d(2)), _video("a1", d(1))]
    feed_b = [_video("b3", d(3)), _video("b1", d(1))]
    merged = merge_feeds([feed_a, feed_b])
    assert [v.video_id for v in merged] == ["b3", "a2", "a1", "b1"]


def test_merge_feeds_none_dates_sort_last():
    d = datetime(2024, 6, 1, tzinfo=timezone.utc)
    merged = merge_feeds([[_video("nodate", None)], [_video("dated", d)]])
    assert [v.video_id for v in merged] == ["dated", "nodate"]


# -- full feed with partial failures --


async def test_feed_partial_failure(cache, monkeypatch):
    fixture = FIXTURES.joinpath("channel_feed.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if "UCgood00000000000000000" in str(request.url):
            return httpx.Response(200, content=fixture)
        return httpx.Response(404)

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _client(httpx.MockTransport(handler))
    )
    source = RSSSource(["UCgood00000000000000000", "UCbad000000000000000000"], cache)
    result = await source.feed()
    assert len(result.videos) == 2
    assert len(result.warnings) == 1
    assert "UCbad000000000000000000" in result.warnings[0]


async def test_feed_offline_falls_back_to_stale_cache(cache, monkeypatch):
    videos = [_video("cached01", datetime(2024, 5, 1, tzinfo=timezone.utc))]
    cache.set_feed("UCoffline000000000000000", videos)
    # Force the entry to be stale.
    cache._conn.execute("UPDATE feed_items SET fetched_at = 0")
    cache._conn.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _client(httpx.MockTransport(handler))
    )
    source = RSSSource(["UCoffline000000000000000"], cache)
    result = await source.feed()
    assert [v.video_id for v in result.videos] == ["cached01"]
    assert result.warnings and "cached" in result.warnings[0]


async def test_feed_uses_fresh_cache_without_network(cache, monkeypatch):
    videos = [_video("fresh001", datetime(2024, 5, 1, tzinfo=timezone.utc))]
    cache.set_feed("UCfresh00000000000000000", videos)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network should not be hit for fresh cache")

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _client(httpx.MockTransport(handler))
    )
    source = RSSSource(["UCfresh00000000000000000"], cache)
    result = await source.feed()
    assert [v.video_id for v in result.videos] == ["fresh001"]
    assert result.warnings == []
