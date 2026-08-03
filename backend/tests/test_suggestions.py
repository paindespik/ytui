"""Suggestions endpoint with mocked related_videos scraping."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from conftest import make_video

from ytui_server.services import ytdlp
from ytui_server.services.suggestions import COOKIE_STALE_WARNING, EMPTY_HISTORY_WARNING


def _watch(client, video_id: str, **kwargs) -> None:
    video = make_video(video_id, **kwargs)
    resp = client.post("/api/history", json={"video": video.model_dump(mode="json")})
    assert resp.status_code == 204


def _related_mock(mapping: dict[str, list]) -> AsyncMock:
    async def fake_related(video_id: str, limit: int = 20):
        return mapping.get(video_id, [])

    return AsyncMock(side_effect=fake_related)


def test_suggestions_empty_history(client):
    resp = client.get("/api/suggestions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["videos"] == []
    assert body["warnings"] == [EMPTY_HISTORY_WARNING]


def test_suggestions_round_robin_and_exclusions(client):
    _watch(client, "seedA0000001", channel_id="UCchanA00000000000000000")
    _watch(client, "seedB0000001", channel_id="UCchanB00000000000000000")
    _watch(client, "oldwatch0001", channel_id="UCchanC00000000000000000")
    mapping = {
        "seedA0000001": [
            make_video("relA1"),
            make_video("relA2"),
            # already watched: must be excluded
            make_video("oldwatch0001"),
        ],
        "seedB0000001": [
            make_video("relB1"),
            # duplicate across sources: kept once
            make_video("relA1"),
            # a seed itself: excluded
            make_video("seedA0000001"),
            make_video("relB2"),
        ],
        "oldwatch0001": [make_video("relC1")],
    }
    with patch(
        "ytui_server.services.suggestions.ytdlp.related_videos",
        new=_related_mock(mapping),
    ):
        resp = client.get("/api/suggestions")
    assert resp.status_code == 200
    body = resp.json()
    ids = [v["video_id"] for v in body["videos"]]
    # Seeds ordered newest-first: oldwatch, seedB, seedA → round-robin tiers.
    assert ids == ["relC1", "relB1", "relA1", "relA2", "relB2"]
    assert body["warnings"] == []


def test_suggestions_seed_dedup_by_channel(client):
    _watch(client, "sameChan0001", channel_id="UCsame000000000000000000")
    _watch(client, "sameChan0002", channel_id="UCsame000000000000000000")
    mapping = {
        "sameChan0001": [make_video("rel10000001")],
        "sameChan0002": [make_video("rel20000001")],
    }
    mock = _related_mock(mapping)
    with patch("ytui_server.services.suggestions.ytdlp.related_videos", new=mock):
        resp = client.get("/api/suggestions")
    assert resp.status_code == 200
    # Only the most recent video per channel is used as a seed.
    assert mock.call_count == 1
    assert mock.call_args.args[0] == "sameChan0002"


def test_suggestions_non_youtube_history_ignored(client):
    _watch(client, "odyvid:1", platform="odysee")
    with patch(
        "ytui_server.services.suggestions.ytdlp.related_videos", new=_related_mock({})
    ) as mock:
        resp = client.get("/api/suggestions")
    assert resp.status_code == 200
    assert mock.call_count == 0
    assert resp.json()["warnings"] == [EMPTY_HISTORY_WARNING]


def test_suggestions_seed_failure_is_warning(client):
    _watch(client, "seedA0000001", channel_id="UCchanA00000000000000000")
    _watch(client, "seedB0000001", channel_id="UCchanB00000000000000000")

    async def flaky(video_id: str, limit: int = 20):
        if video_id == "seedA0000001":
            raise RuntimeError("scrape failed")
        return [make_video("relB1")]

    with patch(
        "ytui_server.services.suggestions.ytdlp.related_videos",
        new=AsyncMock(side_effect=flaky),
    ):
        resp = client.get("/api/suggestions")
    assert resp.status_code == 200
    body = resp.json()
    assert [v["video_id"] for v in body["videos"]] == ["relB1"]
    assert len(body["warnings"]) == 1
    assert "scrape failed" in body["warnings"][0]


def test_suggestions_cached_within_ttl(client):
    _watch(client, "seedA0000001", channel_id="UCchanA00000000000000000")
    mapping = {"seedA0000001": [make_video("relA1")]}
    mock = _related_mock(mapping)
    with patch("ytui_server.services.suggestions.ytdlp.related_videos", new=mock):
        assert client.get("/api/suggestions").status_code == 200
        resp = client.get("/api/suggestions")
        assert mock.call_count == 1  # second call served from cache
        assert [v["video_id"] for v in resp.json()["videos"]] == ["relA1"]
        # refresh=true bypasses the cache
        client.get("/api/suggestions", params={"refresh": "true"})
        assert mock.call_count == 2


def test_suggestions_cache_expires(client, app):
    _watch(client, "seedA0000001", channel_id="UCchanA00000000000000000")
    mapping = {"seedA0000001": [make_video("relA1")]}
    mock = _related_mock(mapping)
    with patch("ytui_server.services.suggestions.ytdlp.related_videos", new=mock):
        assert client.get("/api/suggestions").status_code == 200
        app.state.suggestions_service.ttl_seconds = 0
        assert client.get("/api/suggestions").status_code == 200
        assert mock.call_count == 2



def _with_cookies(app, settings) -> None:
    """Pretend the account cookies were pushed (contents never read: yt-dlp is mocked)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app.state.youtube_cookies.path.write_text("# Netscape HTTP Cookie File\n")
    app.state.suggestions_service.ttl_seconds = 0  # never serve a cached feed


def test_suggestions_use_account_home_when_cookies_present(client, app, settings):
    _watch(client, "seedA0000001", channel_id="UCchanA00000000000000000")
    _with_cookies(app, settings)
    home = AsyncMock(return_value=[make_video("homeVid00001")])
    related = _related_mock({"seedA0000001": [make_video("relA1")]})
    with (
        patch("ytui_server.services.suggestions.ytdlp.home_recommendations", new=home),
        patch("ytui_server.services.suggestions.ytdlp.related_videos", new=related),
    ):
        resp = client.get("/api/suggestions")
    assert resp.status_code == 200
    body = resp.json()
    assert [v["video_id"] for v in body["videos"]] == ["homeVid00001"]
    assert body["warnings"] == []
    related.assert_not_awaited()  # the history path is skipped entirely


def test_suggestions_fall_back_when_home_is_empty(client, app, settings):
    _watch(client, "seedA0000001", channel_id="UCchanA00000000000000000")
    _with_cookies(app, settings)
    related = _related_mock({"seedA0000001": [make_video("relA1")]})
    with (
        patch(
            "ytui_server.services.suggestions.ytdlp.home_recommendations",
            new=AsyncMock(return_value=[]),
        ),
        patch("ytui_server.services.suggestions.ytdlp.related_videos", new=related),
    ):
        resp = client.get("/api/suggestions")
    assert resp.status_code == 200
    body = resp.json()
    assert [v["video_id"] for v in body["videos"]] == ["relA1"]
    assert COOKIE_STALE_WARNING in body["warnings"]


def test_suggestions_fall_back_when_home_fails(client, app, settings):
    _watch(client, "seedA0000001", channel_id="UCchanA00000000000000000")
    _with_cookies(app, settings)
    related = _related_mock({"seedA0000001": [make_video("relA1")]})
    with (
        patch(
            "ytui_server.services.suggestions.ytdlp.home_recommendations",
            new=AsyncMock(side_effect=ytdlp.UpstreamError("cookies rejected")),
        ),
        patch("ytui_server.services.suggestions.ytdlp.related_videos", new=related),
    ):
        resp = client.get("/api/suggestions")
    assert resp.status_code == 200
    body = resp.json()
    assert [v["video_id"] for v in body["videos"]] == ["relA1"]
    assert COOKIE_STALE_WARNING in body["warnings"]