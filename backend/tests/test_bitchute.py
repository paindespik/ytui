"""BitChute channel listing through the public JSON API (HTTP mocked)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx

from ytui_server.services import bitchute, ytdlp

CHAN = "itv_test"


def _item(video_id: str, title: str, **extra) -> dict:
    return {
        "video_id": video_id,
        "video_name": title,
        "date_published": "2026-09-21T18:32:11.555939Z",
        "duration": "4:28",
        "state_id": "published",
        "thumbnail_url": f"https://static-3.bitchute.com/live/cover_images/X/{video_id}_640x360.jpg",
        "view_count": 1119,
        **extra,
    }


ITEMS = [_item(f"vid{i:09d}", f"Video {i}") for i in range(60)]


def _mock_api(items: list[dict]):
    """Patch httpx.AsyncClient.post to serve an offset/limit slice of `items`
    (capped at the API's 50-item page); records every request body."""

    async def fake_post(url, **kwargs):
        body = kwargs["json"]
        calls.append(body)
        start = body["offset"]
        page = items[start : start + min(body["limit"], bitchute.PAGE_SIZE)]
        return httpx.Response(
            200, json={"videos": page}, request=httpx.Request("POST", str(url))
        )

    calls: list[dict] = []
    return patch.object(httpx.AsyncClient, "post", side_effect=fake_post), calls


def test_item_to_video_maps_thumbnail_date_duration():
    video = bitchute._item_to_video(ITEMS[0], CHAN)
    assert video is not None
    assert video.video_id == "vid000000000"
    assert video.title == "Video 0"
    assert video.thumbnail_url.endswith("/vid000000000_640x360.jpg")
    assert video.published is not None and video.published.year == 2026
    assert video.published.utcoffset() is not None
    assert video.duration == 268
    assert video.view_count == 1119
    assert video.channel_id == CHAN
    assert video.platform == "bitchute"
    assert video.url == "https://www.bitchute.com/video/vid000000000/"


def test_parse_duration():
    assert bitchute._parse_duration("3:23:28") == 3 * 3600 + 23 * 60 + 28
    assert bitchute._parse_duration("45") == 45
    assert bitchute._parse_duration("") is None
    assert bitchute._parse_duration(None) is None
    assert bitchute._parse_duration("n/a") is None


def test_channel_videos_endpoint_has_thumbnails(client):
    post_patch, calls = _mock_api(ITEMS)
    with post_patch:
        resp = client.get(
            f"/api/channels/{CHAN}/videos", params={"platform": "bitchute", "limit": 3}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert [i["video_id"] for i in body["items"]] == [
        "vid000000000", "vid000000001", "vid000000002"
    ]
    assert all(i["thumbnail_url"].startswith("https://") for i in body["items"])
    assert all(i["published"] for i in body["items"])
    assert body["has_more"] is True
    assert calls == [{"channel_id": CHAN, "offset": 0, "limit": 4}]


def test_channel_videos_pages_past_api_cap(client):
    """limit + probe > 50 spans two API pages; offset maps 1:1 to the index."""
    post_patch, calls = _mock_api(ITEMS)
    with post_patch:
        resp = client.get(
            f"/api/channels/{CHAN}/videos",
            params={"platform": "bitchute", "limit": 50, "offset": 5},
        )
    body = resp.json()
    assert len(body["items"]) == 50
    assert body["items"][0]["video_id"] == "vid000000005"
    assert body["has_more"] is True
    assert [(c["offset"], c["limit"]) for c in calls] == [(5, 50), (55, 1)]


def test_channel_videos_last_page(client):
    post_patch, _ = _mock_api(ITEMS)
    with post_patch:
        resp = client.get(
            f"/api/channels/{CHAN}/videos",
            params={"platform": "bitchute", "limit": 50, "offset": 50},
        )
    body = resp.json()
    assert len(body["items"]) == 10
    assert body["has_more"] is False


def test_channel_search_scans_api_listing(client):
    items = [_item("aaaaaaaaaaaa", "Cooking pasta"), _item("bbbbbbbbbbbb", "Bread")]
    post_patch, _ = _mock_api(items)
    with post_patch:
        resp = client.get(
            f"/api/channels/{CHAN}/videos", params={"platform": "bitchute", "q": "PASTA"}
        )
    assert [i["video_id"] for i in resp.json()["items"]] == ["aaaaaaaaaaaa"]


def test_api_failure_falls_back_to_ytdlp(client):
    async def boom(url, **kwargs):
        return httpx.Response(
            500, content=json.dumps({"errors": []}), request=httpx.Request("POST", str(url))
        )

    fallback = AsyncMock(return_value=[])
    with (
        patch.object(httpx.AsyncClient, "post", side_effect=boom),
        patch.object(ytdlp, "channel_videos", new=fallback),
    ):
        resp = client.get(f"/api/channels/{CHAN}/videos", params={"platform": "bitchute"})
    assert resp.status_code == 200
    fallback.assert_awaited_once_with(
        f"https://www.bitchute.com/channel/{CHAN}/", limit=51, offset=0
    )
