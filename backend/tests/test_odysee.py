"""Odysee search, channel listing, comments and write guards (HTTP mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx

from ytui_server.models import Video, video_id_from_url
from ytui_server.services import ytdlp

LIGHTHOUSE_HITS = [
    {"name": "linux-video", "claimId": "aa11", "channel": "@chan"},
    {"name": "other-video", "claimId": "bb22", "channel": "@chan"},
]

RESOLVE_RESULT = {
    "lbry://linux-video#aa11": {
        "claim_id": "aa11",
        "name": "linux-video",
        "value_type": "stream",
        "value": {
            "title": "Linux Video",
            "thumbnail": {"url": "https://thumbs.odycdn.com/x.webp"},
            "release_time": "1719000000",
            "video": {"duration": 120},
        },
        "signing_channel": {
            "claim_id": "cc33",
            "name": "@chan",
            "value": {"title": "The Channel"},
        },
    },
    "lbry://other-video#bb22": {
        "claim_id": "bb22",
        "name": "other-video",
        "value_type": "stream",
        "value": {"title": "Other Video"},
    },
}

CLAIM_SEARCH_RESULT = {
    "items": [
        {
            "claim_id": "dd44",
            "name": "chan-video",
            "value_type": "stream",
            "value": {"title": "Chan Video", "video": {"duration": 60}},
            "signing_channel": {"claim_id": "cc33", "name": "@chan"},
        }
    ]
}

COMMENT_LIST_RESULT = {
    "page": 1,
    "page_size": 50,
    "total_items": 2,
    "items": [
        {
            "comment": "Great video",
            "comment_id": "cid1",
            "channel_name": "@viewer",
            "timestamp": 1719000000,
            "replies": 1,
            "is_pinned": False,
        },
        {
            "comment": "Nice",
            "comment_id": "cid2",
            "channel_name": "@other",
            "timestamp": 1719000100,
        },
    ],
}

REACTION_LIST_RESULT = {
    "others_reactions": {"cid1": {"like": 5, "dislike": 1}, "cid2": {"like": 2}}
}


def _rpc(result):
    return {"jsonrpc": "2.0", "result": result, "id": 1}


def _mock_odysee_http(post_results: list[dict], get_json=None):
    """Patch httpx.AsyncClient get/post with canned JSON responses."""
    posts = iter(post_results)

    async def fake_post(url, **kwargs):
        return httpx.Response(
            200, json=next(posts), request=httpx.Request("POST", str(url))
        )

    async def fake_get(url, **kwargs):
        return httpx.Response(
            200, json=get_json, request=httpx.Request("GET", str(url))
        )

    return (
        patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=fake_post)),
        patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=fake_get)),
    )


# ─── /api/search?source=odysee ───


def test_search_odysee(client):
    post_patch, get_patch = _mock_odysee_http(
        [_rpc(RESOLVE_RESULT)], get_json=LIGHTHOUSE_HITS
    )
    with post_patch, get_patch:
        resp = client.get("/api/search", params={"q": "linux", "source": "odysee"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    first = items[0]
    assert first["platform"] == "odysee"
    assert first["video_id"] == "linux-video:aa11"
    assert first["channel_id"] == "@chan:cc33"
    assert first["channel_title"] == "The Channel"
    assert first["duration"] == 120
    assert first["url"] == "https://odysee.com/linux-video:aa11"


def test_search_odysee_upstream_error(client):
    async def fail_get(url, **kwargs):
        raise httpx.ConnectError("down", request=httpx.Request("GET", str(url)))

    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=fail_get)):
        resp = client.get("/api/search", params={"q": "linux", "source": "odysee"})
    assert resp.status_code == 502


def test_search_bad_source(client):
    resp = client.get("/api/search", params={"q": "x", "source": "dailymotion"})
    assert resp.status_code == 422


# ─── /api/channels/{id}/videos?platform=odysee ───


def test_odysee_channel_videos(client):
    post_patch, _ = _mock_odysee_http([_rpc(CLAIM_SEARCH_RESULT)])
    with post_patch:
        resp = client.get(
            "/api/channels/@chan:cc33/videos", params={"platform": "odysee"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["video_id"] == "chan-video:dd44"
    assert body["items"][0]["platform"] == "odysee"
    assert body["channel"]["platform"] == "odysee"


def test_odysee_playlist_rejected(client):
    resp = client.get(
        "/api/ytplaylists/uuid-123/videos", params={"platform": "odysee"}
    )
    assert resp.status_code == 400


# ─── /api/videos/{id}/comments ───


def test_odysee_comments(client):
    post_patch, _ = _mock_odysee_http(
        [_rpc(COMMENT_LIST_RESULT), _rpc(REACTION_LIST_RESULT)]
    )
    with post_patch:
        resp = client.get(
            "/api/videos/linux-video:aa11/comments", params={"platform": "odysee"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    first = body["items"][0]
    assert first["text"] == "Great video"
    assert first["channel_name"] == "@viewer"
    assert first["likes"] == 5
    assert first["dislikes"] == 1
    assert body["items"][1]["likes"] == 2


def test_comments_youtube_not_implemented(client):
    resp = client.get(
        "/api/videos/vid000000001/comments", params={"platform": "youtube"}
    )
    assert resp.status_code == 501


def test_comments_default_platform_is_youtube(client):
    resp = client.get("/api/videos/vid000000001/comments")
    assert resp.status_code == 501


def test_comments_disabled_returns_empty(client):
    disabled = {
        "jsonrpc": "2.0",
        "error": {"code": -32603, "message": "comments are disabled by the creator"},
        "id": 1,
    }
    post_patch, _ = _mock_odysee_http([disabled])
    with post_patch:
        resp = client.get(
            "/api/videos/linux-video:aa11/comments", params={"platform": "odysee"}
        )
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


# ─── like/comment write guards ───


def test_like_odysee_rejected(client):
    resp = client.post(
        "/api/videos/linux-video:aa11/like", params={"platform": "odysee"}
    )
    assert resp.status_code == 409
    assert "wallet" in resp.json()["detail"]


def test_comment_odysee_rejected(client):
    resp = client.post(
        "/api/videos/linux-video:aa11/comment",
        params={"platform": "odysee"},
        json={"text": "hi"},
    )
    assert resp.status_code == 409


# ─── models / entry_to_item ───


def test_video_url_odysee():
    v = Video(video_id="my-video:ab12", title="t", platform="odysee")
    assert v.url == "https://odysee.com/my-video:ab12"
    p = Video(video_id="uuid-123", title="t", platform="odysee", kind="playlist")
    assert p.url == "https://odysee.com/$/playlist/uuid-123"
    c = Video(video_id="@chan:cc33", title="t", platform="odysee", kind="channel")
    assert c.url == "https://odysee.com/@chan:cc33"


def test_video_id_from_url_odysee():
    assert (
        video_id_from_url("https://odysee.com/@chan:cc33/my-video:ab12")
        == "my-video:ab12"
    )
    assert video_id_from_url("https://odysee.com/my-video:ab12") == "my-video:ab12"
    # channel pages and arbitrary paths are not video URLs
    assert video_id_from_url("https://odysee.com/@chan:cc33") is None
    assert video_id_from_url("https://odysee.com/foo") is None
    assert video_id_from_url("https://odysee.com/$/settings") is None


def test_entry_to_item_odysee():
    item = ytdlp.entry_to_item(
        {
            "id": "1bd2a8fec67b8cffa8ae0c983653e50be988ba2f",
            "title": "V",
            "url": "https://odysee.com/@chan:c/my-video:1bd2a8fec67b8cffa8ae0c983653e50be988ba2f",
        }
    )
    assert item is not None
    assert item.platform == "odysee"
    assert item.video_id == "my-video:1bd2a8fec67b8cffa8ae0c983653e50be988ba2f"


def test_entry_to_item_odysee_lbry_uri():
    item = ytdlp.entry_to_item(
        {"id": "ab12", "title": "V", "url": "lbry://my-video#ab12"}
    )
    assert item is not None
    assert item.platform == "odysee"
    assert item.video_id == "my-video:ab12"
