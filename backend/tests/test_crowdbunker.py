"""CrowdBunker channel listing, feed, refs and URL mapping (HTTP mocked)."""

from __future__ import annotations

from unittest.mock import patch

import httpx

from ytui_server.models import Video, video_id_from_url
from ytui_server.services import crowdbunker

ORG = "PruneDePrune"
ORG_NAME = "prune de prune"

POSTS_PAGE_1 = {
    "last": "2025-01-03T21:20:13+01:00",
    "items": [
        {
            "uid": "vid000001",
            "video": {
                "title": "Latest video",
                "duration": 95,
                "censored": False,
                "thumbnails": [
                    {"url": "https://img.crowdbunker.com/a/default.jpg"},
                    {"url": "https://img.crowdbunker.com/a/maxres.jpg"},
                ],
            },
            "publishedAt": "2026-03-28T13:12:41+01:00",
            "organization": {"uid": ORG, "name": ORG_NAME},
        },
        # Text post: no video object, must be dropped
        {
            "uid": "txt00002",
            "video": {},
            "publishedAt": "2026-02-01T00:00:00+01:00",
            "organization": {"uid": ORG, "name": ORG_NAME},
        },
        {
            "uid": "vid00003",
            "video": {
                "title": "Second video",
                "duration": None,
                "censored": False,
                "thumbnails": [],
            },
            "publishedAt": "2026-01-15T10:00:00+01:00",
            "organization": {"uid": ORG, "name": ORG_NAME},
        },
        {
            "uid": "vid00004",
            "video": {
                "title": "Censored video",
                "duration": 60,
                "censored": True,
                "thumbnails": [],
            },
            "publishedAt": "2026-01-10T10:00:00+01:00",
            "organization": {"uid": ORG, "name": ORG_NAME},
        },
    ],
}

POSTS_PAGE_2 = {
    "last": None,
    "items": [
        {
            "uid": "vid00005",
            "video": {"title": "Old video", "duration": 30, "censored": False, "thumbnails": []},
            "publishedAt": "2025-12-01T00:00:00+01:00",
            "organization": {"uid": ORG, "name": ORG_NAME},
        },
    ],
}


def _mock_cb_http(pages: list[dict]):
    """Patch httpx.AsyncClient.get to serve the given posts pages in order."""

    def fake_get(url, **kwargs):
        page = pages[min(calls[0], len(pages) - 1)]
        calls[0] += 1
        return httpx.Response(200, json=page, request=httpx.Request("GET", str(url)))

    calls = [0]
    return patch.object(httpx.AsyncClient, "get", side_effect=fake_get), calls


# ─── URL / id mapping ───


def test_video_url_and_id_roundtrip():
    video = Video(video_id="vid000001", title="t", platform="crowdbunker")
    assert video.url == "https://crowdbunker.com/v/vid000001"
    assert video_id_from_url("https://crowdbunker.com/v/vid000001") == "vid000001"
    channel = Video(video_id=ORG, title="", kind="channel", platform="crowdbunker")
    assert channel.url == f"https://crowdbunker.com/@{ORG}"


# ─── post mapping ───


def test_post_to_video_drops_non_video_posts():
    item = POSTS_PAGE_1["items"][0]
    video = crowdbunker._post_to_video(item, ORG)
    assert video is not None
    assert video.video_id == "vid000001"
    assert video.title == "Latest video"
    assert video.duration == 95
    assert video.channel_id == ORG
    assert video.channel_title == ORG_NAME
    assert video.platform == "crowdbunker"
    assert video.published is not None and video.published.utcoffset() is not None
    assert video.thumbnail_url == "https://img.crowdbunker.com/a/default.jpg"
    assert crowdbunker._post_to_video(POSTS_PAGE_1["items"][1], ORG) is None  # text post
    assert crowdbunker._post_to_video(POSTS_PAGE_1["items"][3], ORG) is None  # censored


# ─── /api/channels/{id}/videos?platform=crowdbunker ───


def test_crowdbunker_channel_videos(client):
    get_patch, _ = _mock_cb_http([POSTS_PAGE_1, POSTS_PAGE_2])
    with get_patch:
        resp = client.get(f"/api/channels/{ORG}/videos", params={"platform": "crowdbunker"})
    assert resp.status_code == 200
    body = resp.json()
    assert [i["video_id"] for i in body["items"]] == ["vid000001", "vid00003", "vid00005"]
    assert body["channel"]["platform"] == "crowdbunker"
    assert body["has_more"] is False


def test_crowdbunker_channel_videos_pagination(client):
    """offset counts kept videos across cursor pages (text posts skipped)."""
    get_patch, _ = _mock_cb_http([POSTS_PAGE_1, POSTS_PAGE_2])
    with get_patch:
        resp = client.get(
            f"/api/channels/{ORG}/videos",
            params={"platform": "crowdbunker", "limit": 2, "offset": 1},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert [i["video_id"] for i in body["items"]] == ["vid00003", "vid00005"]
    assert body["has_more"] is False


def test_crowdbunker_channel_not_found(client):
    async def not_found(url, **kwargs):
        return httpx.Response(404, json={"detail": "Not Found"}, request=httpx.Request("GET", str(url)))

    with patch.object(httpx.AsyncClient, "get", side_effect=not_found):
        resp = client.get("/api/channels/nobody/videos", params={"platform": "crowdbunker"})
    assert resp.status_code == 502


def test_crowdbunker_channel_search_short_query(client):
    """Short queries are served by the local title scan, not the (absent) search API."""
    get_patch, _ = _mock_cb_http([POSTS_PAGE_1, POSTS_PAGE_2])
    with get_patch:
        resp = client.get(
            f"/api/channels/{ORG}/videos",
            params={"platform": "crowdbunker", "q": "second"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert [i["video_id"] for i in body["items"]] == ["vid00003"]


# ─── channel refs (POST /api/channels) ───


def test_add_crowdbunker_channel_by_prefix(client):
    get_patch, _ = _mock_cb_http([POSTS_PAGE_1])
    with get_patch:
        resp = client.post("/api/channels", json={"ref": f"crowdbunker:{ORG}"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["channel_id"] == ORG
    assert body["title"] == ORG_NAME
    assert body["platform"] == "crowdbunker"


def test_add_crowdbunker_channel_by_url(client):
    get_patch, _ = _mock_cb_http([POSTS_PAGE_1])
    with get_patch:
        resp = client.post("/api/channels", json={"ref": f"https://crowdbunker.com/@{ORG}"})
    assert resp.status_code == 201
    assert resp.json()["channel_id"] == ORG


def test_add_unknown_crowdbunker_channel(client):
    async def not_found(url, **kwargs):
        return httpx.Response(404, json={"detail": "Not Found"}, request=httpx.Request("GET", str(url)))

    with patch.object(httpx.AsyncClient, "get", side_effect=not_found):
        resp = client.post("/api/channels", json={"ref": "crowdbunker:NoSUCHchannel"})
    assert resp.status_code == 404


# ─── home feed ───


def test_feed_includes_crowdbunker(client):
    get_patch, _ = _mock_cb_http([POSTS_PAGE_1, POSTS_PAGE_1])
    with get_patch:
        client.post("/api/channels", json={"ref": f"crowdbunker:{ORG}"})
        resp = client.get("/api/feed", params={"refresh": True})
    assert resp.status_code == 200
    ids = [v["video_id"] for v in resp.json()["videos"]]
    assert "vid000001" in ids and "vid00003" in ids
    # text and censored posts stay out of the feed
    assert "txt00002" not in ids and "vid00004" not in ids
