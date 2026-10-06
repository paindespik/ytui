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


# ─── Channel playlists (/profile/playlists) and playlist videos ───

PROFILE = "prof00000001"


def _playlist(index: int, **extra) -> dict:
    return {
        "playlist_id": f"pl{index:010d}",
        "playlist_name": f"Playlist {index}",
        "date_created": "2025-11-24T06:39:40.456392Z",
        "playlist_url": f"/playlist/pl{index:010d}/",
        "thumbnail_url": f"https://static-3.bitchute.com/live/playlist_images/pl{index:010d}/t.jpg",
        "video_count": 3,
        "is_public": True,
        "is_restricted": False,
        **extra,
    }


PLAYLISTS = [_playlist(i) for i in range(25)]


def _mock_profile_api(playlists: list[dict], channel_status: int = 200):
    """Serve /channel then fixed 10-item /profile/playlists pages (the API
    ignores `limit`); records (path, body) for every request."""

    async def fake_post(url, **kwargs):
        body = kwargs["json"]
        path = str(url).removeprefix(bitchute.API_BASE + "/")
        calls.append((path, body))
        request = httpx.Request("POST", str(url))
        if path == "channel":
            if channel_status != 200:
                return httpx.Response(channel_status, json={"errors": []}, request=request)
            return httpx.Response(
                200,
                json={"channel_id": "Hash00000001", "channel_name": "ITV",
                      "profile_id": PROFILE},
                request=request,
            )
        assert path == "profile/playlists"
        start = body["offset"]
        page = playlists[start : start + bitchute.PLAYLISTS_PAGE_SIZE]
        return httpx.Response(
            200, json={"playlists": page, "playlist_count": len(playlists)}, request=request
        )

    calls: list[tuple[str, dict]] = []
    return patch.object(httpx.AsyncClient, "post", side_effect=fake_post), calls


def test_channel_playlists_endpoint(client):
    post_patch, calls = _mock_profile_api(PLAYLISTS)
    with post_patch:
        resp = client.get(
            f"/api/channels/{CHAN}/playlists", params={"platform": "bitchute", "limit": 12}
        )
    assert resp.status_code == 200
    body = resp.json()
    items = body["items"]
    assert len(items) == 12
    first = items[0]
    assert first["kind"] == "playlist"
    assert first["platform"] == "bitchute"
    assert first["video_id"] == "pl0000000000"
    assert first["title"] == "Playlist 0"
    assert first["channel_title"] == "ITV"
    assert first["channel_id"] == CHAN
    assert first["thumbnail_url"].startswith("https://")
    assert first["url"] == "https://www.bitchute.com/playlist/pl0000000000/"
    assert body["has_more"] is True
    assert body["channel"]["title"] == "ITV"
    # Channel lookup, then the profile's 10-item pages until limit + probe.
    assert calls[0] == ("channel", {"channel_id": CHAN})
    assert [b["offset"] for p, b in calls[1:]] == [0, 10]
    assert all(b["profile_id"] == PROFILE for _, b in calls[1:])


def test_channel_playlists_last_page_stops_at_count(client):
    post_patch, calls = _mock_profile_api(PLAYLISTS)
    with post_patch:
        resp = client.get(
            f"/api/channels/{CHAN}/playlists",
            params={"platform": "bitchute", "limit": 50, "offset": 20},
        )
    body = resp.json()
    assert [i["video_id"] for i in body["items"]] == [f"pl{i:010d}" for i in range(20, 25)]
    assert body["has_more"] is False
    # playlist_count reached after one page: no empty trailing request.
    assert [b["offset"] for p, b in calls[1:]] == [20]


def test_channel_playlists_skip_private(client):
    post_patch, _ = _mock_profile_api([_playlist(0, is_public=False), _playlist(1)])
    with post_patch:
        resp = client.get(f"/api/channels/{CHAN}/playlists", params={"platform": "bitchute"})
    assert [i["video_id"] for i in resp.json()["items"]] == ["pl0000000001"]


def test_channel_playlists_unknown_channel(client):
    post_patch, _ = _mock_profile_api(PLAYLISTS, channel_status=404)
    with post_patch:
        resp = client.get(f"/api/channels/{CHAN}/playlists", params={"platform": "bitchute"})
    assert resp.status_code == 404


def test_channel_playlists_upstream_error(client):
    post_patch, _ = _mock_profile_api(PLAYLISTS, channel_status=500)
    with post_patch:
        resp = client.get(f"/api/channels/{CHAN}/playlists", params={"platform": "bitchute"})
    assert resp.status_code == 502


def _playlist_video(index: int) -> dict:
    item = _item(f"pv{index:010d}", f"Entry {index}")
    item["channel"] = {"channel_id": "Hash00000001", "channel_name": "ITV"}
    return {"position": index + 1, "video": item}


def _mock_playlist_api(entries: list[dict]):
    async def fake_post(url, **kwargs):
        body = kwargs["json"]
        path = str(url).removeprefix(bitchute.API_BASE + "/")
        calls.append((path, body))
        request = httpx.Request("POST", str(url))
        if path == "playlist":
            return httpx.Response(
                200, json={"playlist_id": body["playlist_id"], "playlist_name": "NASA - ISS"},
                request=request,
            )
        assert path == "playlist/videos"
        start = body["offset"]
        page = entries[start : start + min(body["limit"], bitchute.PLAYLIST_VIDEOS_PAGE_SIZE)]
        return httpx.Response(
            200, json={"videos": page, "video_count": len(entries)}, request=request
        )

    calls: list[tuple[str, dict]] = []
    return patch.object(httpx.AsyncClient, "post", side_effect=fake_post), calls


def test_playlist_videos_use_api(client):
    entries = [_playlist_video(i) for i in range(70)]
    post_patch, calls = _mock_playlist_api(entries)
    with post_patch:
        resp = client.get(
            "/api/ytplaylists/bp8bmp39FmfD/videos", params={"platform": "bitchute"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "NASA - ISS"
    assert len(body["items"]) == 70
    first = body["items"][0]
    assert first["video_id"] == "pv0000000000"
    assert first["thumbnail_url"].startswith("https://")
    assert first["published"]
    assert first["channel_title"] == "ITV"
    assert first["channel_id"] == "Hash00000001"
    assert [b["offset"] for p, b in calls if p == "playlist/videos"] == [0, 50]


def test_playlist_videos_fall_back_to_ytdlp(client):
    async def boom(url, **kwargs):
        return httpx.Response(500, json={"errors": []}, request=httpx.Request("POST", str(url)))

    fallback = AsyncMock(return_value=([], "From yt-dlp"))
    with (
        patch.object(httpx.AsyncClient, "post", side_effect=boom),
        patch.object(ytdlp, "playlist_videos", new=fallback),
    ):
        resp = client.get(
            "/api/ytplaylists/bp8bmp39FmfD/videos", params={"platform": "bitchute"}
        )
    assert resp.status_code == 200
    assert resp.json()["title"] == "From yt-dlp"
    fallback.assert_awaited_once_with(
        "https://www.bitchute.com/playlist/bp8bmp39FmfD/", limit=200
    )


def test_import_bitchute_playlist_keeps_thumbnails(client):
    post_patch, _ = _mock_playlist_api([_playlist_video(i) for i in range(3)])
    with post_patch:
        resp = client.post(
            "/api/playlists/import",
            json={"source": "https://www.bitchute.com/playlist/bp8bmp39FmfD/",
                  "platform": "bitchute"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["playlist"]["name"] == "NASA - ISS"
    assert body["added"] == 3
    items = client.get(f"/api/playlists/{body['playlist']['id']}/items").json()
    assert all(i["video"]["thumbnail_url"].startswith("https://") for i in items)
