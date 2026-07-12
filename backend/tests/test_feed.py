"""Feed endpoint with mocked RSS fetching."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

FIXTURES = Path(__file__).parent / "fixtures"


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
