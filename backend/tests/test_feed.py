"""Feed endpoint with mocked RSS fetching."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ytui_server.services import ytdlp

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_view_count_enrichment(monkeypatch):
    """Feed tests stay hermetic: no yt-dlp /videos fetch by default.

    Tests that care about enrichment re-patch ytdlp.channel_videos.
    """

    async def _none(channel_url: str, limit: int = 50, offset: int = 0):
        return []

    monkeypatch.setattr(ytdlp, "channel_videos", _none)


def _mock_get(responses: dict[str, httpx.Response]):
    """AsyncClient.get mock keyed by substring of the URL."""

    async def fake_get(url, **kwargs):
        for key, resp in responses.items():
            if key in str(url):
                resp.request = httpx.Request("GET", str(url))
                return resp
        raise httpx.ConnectError("no route", request=httpx.Request("GET", str(url)))

    return AsyncMock(side_effect=fake_get)


def test_feed_empty_without_channels(client):
    resp = client.get("/api/feed")
    assert resp.status_code == 200
    assert resp.json() == {"videos": [], "warnings": []}


def test_feed_returns_channel_videos(client, app):
    xml = (FIXTURES / "channel_feed.xml").read_bytes()
    from ytui_server.models import FollowedChannel

    app.state.db.add_channel(
        FollowedChannel(ref="UCtest000000000000000000", channel_id="UCtest000000000000000000")
    )
    with patch.object(
        httpx.AsyncClient, "get", _mock_get({"feeds/videos.xml": httpx.Response(200, content=xml)})
    ):
        resp = client.get("/api/feed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["warnings"] == []
    assert len(body["videos"]) == 2
    video = body["videos"][0]
    assert video["video_id"]
    assert video["url"].startswith("https://www.youtube.com/watch?v=")


def test_feed_serves_cache_within_ttl(client, app):
    xml = (FIXTURES / "channel_feed.xml").read_bytes()
    from ytui_server.models import FollowedChannel

    app.state.db.add_channel(
        FollowedChannel(ref="UCtest000000000000000000", channel_id="UCtest000000000000000000")
    )
    mock = _mock_get({"feeds/videos.xml": httpx.Response(200, content=xml)})
    with patch.object(httpx.AsyncClient, "get", mock):
        client.get("/api/feed")
        client.get("/api/feed")  # second call served from cache
    assert mock.call_count == 1


def test_feed_offline_fallback_with_warning(client, app):
    xml = (FIXTURES / "channel_feed.xml").read_bytes()
    from ytui_server.models import FollowedChannel

    app.state.db.add_channel(
        FollowedChannel(ref="UCtest000000000000000000", channel_id="UCtest000000000000000000")
    )
    with patch.object(
        httpx.AsyncClient, "get", _mock_get({"feeds/videos.xml": httpx.Response(200, content=xml)})
    ):
        client.get("/api/feed")
    # now upstream is down; force refresh should fall back to stale cache
    with patch.object(httpx.AsyncClient, "get", _mock_get({})):
        resp = client.get("/api/feed", params={"refresh": True})
    body = resp.json()
    assert len(body["videos"]) == 2
    assert len(body["warnings"]) == 1
    assert "cached" in body["warnings"][0]


def test_feed_odysee_channel(client, app):
    xml = (FIXTURES / "odysee_feed.xml").read_bytes()
    from ytui_server.models import FollowedChannel

    app.state.db.add_channel(
        FollowedChannel(
            ref="odysee:@testchan:1", channel_id="@testchan:1", platform="odysee"
        )
    )
    with patch.object(
        httpx.AsyncClient,
        "get",
        _mock_get({"odysee.com/$/rss": httpx.Response(200, content=xml)}),
    ):
        resp = client.get("/api/feed")
    body = resp.json()
    assert resp.status_code == 200
    # the fixture's third item is a PDF enclosure: not playable, must be dropped
    assert len(body["videos"]) == 2
    assert all(v["platform"] == "odysee" for v in body["videos"])
    video = body["videos"][0]
    assert video["video_id"] == "first-odysee-video:1bd2a8fec67b8cffa8ae0c983653e50be988ba2f"
    assert video["url"].startswith("https://odysee.com/")
    assert video["thumbnail_url"] == "https://thumbs.odycdn.com/aaa111.webp"
    assert video["channel_title"] == "testchan on Odysee"


def test_feed_bitchute_channel(client, app):
    xml = (FIXTURES / "bitchute_feed.xml").read_bytes()
    from ytui_server.models import FollowedChannel

    app.state.db.add_channel(
        FollowedChannel(
            ref="bitchute:testchan", channel_id="testchan", platform="bitchute"
        )
    )
    with patch.object(
        httpx.AsyncClient,
        "get",
        _mock_get({"api.bitchute.com": httpx.Response(200, content=xml)}),
    ):
        resp = client.get("/api/feed")
    body = resp.json()
    assert resp.status_code == 200
    assert body["videos"], body
    assert all(v["platform"] == "bitchute" for v in body["videos"])
    assert body["videos"][0]["url"].startswith("https://www.bitchute.com/video/")


def test_feed_youtube_videos_carry_view_counts(client, app):
    """RSS items gain view counts from the parallel /videos lookup."""
    from ytui_server.models import FollowedChannel, Video

    xml = (FIXTURES / "channel_feed.xml").read_bytes()
    app.state.db.add_channel(
        FollowedChannel(ref="UCtest000000000000000000", channel_id="UCtest000000000000000000")
    )

    async def fake_counts(channel_url: str, limit: int = 50, offset: int = 0):
        assert "/channel/UCtest000000000000000000" in channel_url
        return [
            Video(video_id="abc123def45", title="A", view_count=1234),
            Video(video_id="xyz987wvu65", title="B"),  # no count: stays None
        ]

    with (
        patch.object(ytdlp, "channel_videos", fake_counts),
        patch.object(
            httpx.AsyncClient,
            "get",
            _mock_get({"feeds/videos.xml": httpx.Response(200, content=xml)}),
        ),
    ):
        resp = client.get("/api/feed")
    body = resp.json()
    assert resp.status_code == 200
    counts = {v["video_id"]: v["view_count"] for v in body["videos"]}
    assert counts["abc123def45"] == 1234
    assert counts["xyz987wvu65"] is None


def test_feed_enrichment_failure_keeps_feed(client, app):
    """The /videos lookup is enrichment only: its failure must not fail the feed."""
    from ytui_server.models import FollowedChannel

    xml = (FIXTURES / "channel_feed.xml").read_bytes()
    app.state.db.add_channel(
        FollowedChannel(ref="UCtest000000000000000000", channel_id="UCtest000000000000000000")
    )

    async def _broken(channel_url: str, limit: int = 50, offset: int = 0):
        raise RuntimeError("upstream down")

    with (
        patch.object(ytdlp, "channel_videos", _broken),
        patch.object(
            httpx.AsyncClient,
            "get",
            _mock_get({"feeds/videos.xml": httpx.Response(200, content=xml)}),
        ),
    ):
        resp = client.get("/api/feed")
    body = resp.json()
    assert resp.status_code == 200
    assert body["warnings"] == []
    assert {v["video_id"] for v in body["videos"]} == {"abc123def45", "xyz987wvu65"}
    assert all(v["view_count"] is None for v in body["videos"])


def test_feed_crowdbunker_videos_carry_view_counts(client, app):
    from ytui_server.models import FollowedChannel

    app.state.db.add_channel(
        FollowedChannel(
            ref="crowdbunker:testorg", channel_id="testorg", platform="crowdbunker"
        )
    )
    items = [
        {
            "uid": "uid1234567890",
            "video": {
                "id": 1,
                "title": "A video",
                "duration": 120,
                "viewCount": 987,
                "thumbnails": [{"url": "https://example.com/t.jpg"}],
            },
            "organization": {"name": "TestOrg"},
            "publishedAt": "2026-01-10T12:00:00Z",
        },
    ]
    with patch.object(
        httpx.AsyncClient,
        "get",
        _mock_get(
            {"api.crowdbunker.com": httpx.Response(200, json={"items": items, "last": None})}
        ),
    ):
        resp = client.get("/api/feed")
    body = resp.json()
    assert resp.status_code == 200
    assert body["videos"], body
    assert body["videos"][0]["view_count"] == 987
