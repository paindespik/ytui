"""Odysee search, channel listing, comments and write guards (HTTP mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx

from ytui_server.models import StreamInfo, Video, VideoDetails, video_id_from_url
from ytui_server.services import odysee, ytdlp

LIGHTHOUSE_HITS = [
    {"name": "linux-video", "claimId": "aa11", "channel": "@chan"},
    {"name": "other-video", "claimId": "bb22", "channel": "@chan"},
    {"name": "a-pdf", "claimId": "ee55", "channel": "@chan"},
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
            "stream_type": "video",
            "source": {"media_type": "video/mp4"},
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
        "value": {
            "title": "Other Video",
            "stream_type": "video",
            "source": {"media_type": "video/mp4"},
        },
    },
    # Odysee channels also publish documents/images: not playable, must be dropped
    "lbry://a-pdf#ee55": {
        "claim_id": "ee55",
        "name": "a-pdf",
        "value_type": "stream",
        "value": {
            "title": "A Document",
            "stream_type": "document",
            "source": {"media_type": "application/pdf"},
        },
    },
}

CLAIM_SEARCH_RESULT = {
    "items": [
        {
            "claim_id": "dd44",
            "name": "chan-video",
            "value_type": "stream",
            "value": {
                "title": "Chan Video",
                "stream_type": "video",
                "source": {"media_type": "video/mp4"},
                "video": {"duration": 60},
            },
            "signing_channel": {"claim_id": "cc33", "name": "@chan"},
        },
        {
            "claim_id": "ff66",
            "name": "chan-image",
            "value_type": "stream",
            "value": {
                "title": "Chan Image",
                "stream_type": "image",
                "source": {"media_type": "image/jpeg"},
            },
            "signing_channel": {"claim_id": "cc33", "name": "@chan"},
        },
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


def _mock_proxy(methods: dict):
    """Patch the SDK proxy post, dispatching on the JSON-RPC method name."""

    async def fake_post(url, **kwargs):
        method = (kwargs.get("json") or {}).get("method")
        return httpx.Response(
            200, json=_rpc(methods[method]), request=httpx.Request("POST", str(url))
        )

    return patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=fake_post))


def _mock_head(final_url: str, content_type: str):
    """Patch httpx HEAD: report the redirect already followed (final URL)."""

    async def fake_head(url, **kwargs):
        return httpx.Response(
            200,
            headers={"content-type": content_type},
            request=httpx.Request("HEAD", final_url),
        )

    return patch.object(httpx.AsyncClient, "head", AsyncMock(side_effect=fake_head))


def _no_ytdlp(name: str):
    return patch.object(
        ytdlp, name, AsyncMock(side_effect=AssertionError(f"{name} must not be called"))
    )


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
    assert [i["video_id"] for i in items] == ["linux-video:aa11", "other-video:bb22"]
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
    items = body["items"]
    assert [i["video_id"] for i in items] == ["chan-video:dd44"]  # image claim dropped
    assert items[0]["platform"] == "odysee"
    assert body["channel"]["platform"] == "odysee"


def test_odysee_channel_videos_requests_media_only(client):
    """claim_search must filter server-side, else pagination returns short pages."""
    seen: list[dict] = []

    async def fake_post(url, **kwargs):
        seen.append(kwargs["json"]["params"])
        return httpx.Response(
            200, json=_rpc(CLAIM_SEARCH_RESULT), request=httpx.Request("POST", str(url))
        )

    with patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=fake_post)):
        client.get("/api/channels/@chan:cc33/videos", params={"platform": "odysee"})
    assert seen[0]["stream_types"] == ["video", "audio"]


# ─── /api/channels/{id}/videos?platform=odysee&sort=views ───

# Newest first, as claim_search returns them; the ranking must reorder them.
POPULAR_CLAIMS = {
    "items": [
        {
            "claim_id": "vv33",
            "name": "new-quiet",
            "value_type": "stream",
            "value": {
                "title": "New Quiet",
                "stream_type": "video",
                "source": {"media_type": "video/mp4"},
                "release_time": "1719000100",
            },
            "signing_channel": {"claim_id": "cc33", "name": "@chan"},
        },
        {
            "claim_id": "vv22",
            "name": "new-viral",
            "value_type": "stream",
            "value": {
                "title": "New Viral",
                "stream_type": "video",
                "source": {"media_type": "video/mp4"},
                "release_time": "1719000000",
            },
            "signing_channel": {"claim_id": "cc33", "name": "@chan"},
        },
        {
            "claim_id": "vv11",
            "name": "old-popular",
            "value_type": "stream",
            "value": {
                "title": "Old Popular",
                "stream_type": "video",
                "source": {"media_type": "video/mp4"},
                "release_time": "1700000000",
            },
            "signing_channel": {"claim_id": "cc33", "name": "@chan"},
        },
    ]
}


def _mock_popular_http(view_counts=None, view_count_statuses=(200,)):
    """Route posts by URL: SDK proxy (claim_search) and api.lbry.com.

    `view_counts` maps claim id -> count (unknown ids answer 0, like the API).
    Returns (patch, calls) where calls lists the posted URLs in order.
    """
    calls: list[str] = []
    statuses = list(view_count_statuses)

    async def fake_post(url, **kwargs):
        url = str(url)
        calls.append(url)
        if "api.lbry.com" in url:
            if url.endswith("/user/new"):
                body = {"success": True, "data": {"id": 1, "auth_token": "lbrytok"}}
                return httpx.Response(200, json=body, request=httpx.Request("POST", url))
            status = statuses.pop(0) if len(statuses) > 1 else statuses[0]  # /file/view_count
            csv = (kwargs.get("data") or {}).get("claim_id", "")
            body = {
                "success": True,
                "data": [
                    (view_counts or {}).get(claim_id, 0) for claim_id in csv.split(",")
                ],
            }
            return httpx.Response(status, json=body, request=httpx.Request("POST", url))
        return httpx.Response(
            200, json=_rpc(POPULAR_CLAIMS), request=httpx.Request("POST", url)
        )

    return (
        patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=fake_post)),
        calls,
    )


def test_odysee_videos_by_views(client):
    post_patch, calls = _mock_popular_http(
        view_counts={"vv11": 100, "vv22": 5000, "vv33": 500}
    )
    with post_patch:
        resp = client.get(
            "/api/channels/@chan:cc33/videos",
            params={"platform": "odysee", "sort": "views"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert [i["video_id"] for i in body["items"]] == [
        "new-viral:vv22",
        "new-quiet:vv33",
        "old-popular:vv11",
    ]
    assert [i["view_count"] for i in body["items"]] == [5000, 500, 100]
    # One anonymous LBRY user serves the whole request.
    assert sum("user/new" in c for c in calls) == 1


def test_odysee_videos_by_views_pages_share_cached_order(client):
    post_patch, calls = _mock_popular_http(
        view_counts={"vv11": 100, "vv22": 5000, "vv33": 500}
    )
    with post_patch:
        first = client.get(
            "/api/channels/@chan:cc33/videos",
            params={"platform": "odysee", "sort": "views", "limit": 2},
        ).json()
        second = client.get(
            "/api/channels/@chan:cc33/videos",
            params={"platform": "odysee", "sort": "views", "limit": 2, "offset": 2},
        ).json()
    assert [i["video_id"] for i in first["items"]] == [
        "new-viral:vv22",
        "new-quiet:vv33",
    ]
    assert first["has_more"] is True
    assert [i["video_id"] for i in second["items"]] == ["old-popular:vv11"]
    assert second["has_more"] is False
    # The later page slices the cached ranking: one upstream claim_search walk.
    assert len([c for c in calls if "na-backend.odysee.com" in c]) == 1


def test_odysee_videos_by_views_lbry_down_keeps_date_order(client):
    """LBRY is the count source, not the listing: its failure degrades, no 502."""
    post_patch, _ = _mock_popular_http(view_count_statuses=(500,))
    with post_patch:
        resp = client.get(
            "/api/channels/@chan:cc33/videos",
            params={"platform": "odysee", "sort": "views"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert [i["video_id"] for i in body["items"]] == [
        "new-quiet:vv33",
        "new-viral:vv22",
        "old-popular:vv11",
    ]
    assert all(i["view_count"] is None for i in body["items"])


def test_odysee_videos_by_views_token_refresh_on_401(client):
    post_patch, calls = _mock_popular_http(
        view_counts={"vv11": 100, "vv22": 5000, "vv33": 500},
        view_count_statuses=(401, 200),
    )
    with post_patch:
        resp = client.get(
            "/api/channels/@chan:cc33/videos",
            params={"platform": "odysee", "sort": "views"},
        )
    assert resp.status_code == 200
    assert resp.json()["items"][0]["video_id"] == "new-viral:vv22"  # retry succeeded
    # The expired token was recreated before the retry.
    assert sum("user/new" in c for c in calls) == 2


def test_odysee_channel_videos_query_scopes_lighthouse(client):
    """claim_search has no full-text filter, so `q` goes through Lighthouse."""
    seen: list[dict] = []

    async def fake_get(url, **kwargs):
        seen.append(kwargs["params"])
        return httpx.Response(
            200, json=LIGHTHOUSE_HITS, request=httpx.Request("GET", str(url))
        )

    async def fake_post(url, **kwargs):
        return httpx.Response(
            200, json=_rpc(RESOLVE_RESULT), request=httpx.Request("POST", str(url))
        )

    with (
        patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=fake_get)),
        patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=fake_post)),
    ):
        resp = client.get(
            "/api/channels/@chan:cc33/videos",
            params={"platform": "odysee", "q": "linux", "limit": 5, "offset": 10},
        )
    assert resp.status_code == 200
    assert seen[0]["channel_id"] == "cc33"
    assert seen[0]["s"] == "linux"
    assert seen[0]["from"] == 10
    assert [i["video_id"] for i in resp.json()["items"]] == [
        "linux-video:aa11",
        "other-video:bb22",
    ]


def test_odysee_search_never_asks_lighthouse_for_more_than_50(client):
    """Lighthouse 400s on `size` > 50; the channel probe (limit+1=51) must chunk."""
    seen: list[dict] = []
    pages = [
        [{"name": f"v{i}", "claimId": f"c{i:02x}"} for i in range(50)],
        [{"name": "last", "claimId": "ff00"}],
    ]

    async def fake_get(url, **kwargs):
        seen.append(kwargs["params"])
        return httpx.Response(
            200, json=pages.pop(0), request=httpx.Request("GET", str(url))
        )

    async def fake_post(url, **kwargs):
        return httpx.Response(
            200, json=_rpc(RESOLVE_RESULT), request=httpx.Request("POST", str(url))
        )

    with (
        patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=fake_get)),
        patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=fake_post)),
    ):
        resp = client.get(
            "/api/channels/@chan:cc33/videos",
            params={"platform": "odysee", "q": "linux", "limit": 50},
        )
    assert resp.status_code == 200
    assert [p["size"] for p in seen] == [50, 1]
    assert seen[1]["from"] == 50
    assert seen[1]["channel_id"] == "cc33"


def test_odysee_channel_videos_short_query_local_scan(client):
    """Lighthouse 400s on queries under 3 chars; fall back to a title scan."""
    lighthouse_hits: list[dict] = []

    async def fake_get(url, **kwargs):
        # If Lighthouse were called with a short query it would 400: record and
        # fail loudly instead.
        lighthouse_hits.append(kwargs["params"])
        return httpx.Response(
            400, json={"success": False}, request=httpx.Request("GET", str(url))
        )

    async def fake_post(url, **kwargs):
        return httpx.Response(
            200, json=_rpc(CLAIM_SEARCH_RESULT), request=httpx.Request("POST", str(url))
        )

    with (
        patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=fake_get)),
        patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=fake_post)),
    ):
        resp = client.get(
            "/api/channels/@chan:cc33/videos",
            params={"platform": "odysee", "q": "ch", "limit": 5},
        )
    assert lighthouse_hits == []  # short query never reaches Lighthouse
    assert resp.status_code == 200
    assert [i["video_id"] for i in resp.json()["items"]] == ["chan-video:dd44"]


def test_odysee_search_short_query_empty(client):
    """A short global Odysee search comes back empty, not a 502."""

    async def boom_get(url, **kwargs):
        raise AssertionError("Lighthouse must not be called for short queries")

    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=boom_get)):
        resp = client.get("/api/search", params={"q": "vs", "source": "odysee"})
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ─── /api/videos/{id}?platform=odysee (details via proxy, no yt-dlp) ───


def test_odysee_details_via_proxy(client):
    """Details come from the proxy `resolve`: yt-dlp must never be called."""
    with _mock_proxy({"resolve": RESOLVE_RESULT}), _no_ytdlp("video_details"):
        resp = client.get("/api/videos/linux-video:aa11", params={"platform": "odysee"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Linux Video"
    assert body["channel_id"] == "@chan:cc33"
    assert body["channel_title"] == "The Channel"
    assert body["duration"] == 120
    assert body["upload_date"] == "2024-06-21"


def test_odysee_details_unplayable(client):
    """A known document/image claim is a 415, not a yt-dlp failure."""
    with _mock_proxy({"resolve": RESOLVE_RESULT}), _no_ytdlp("video_details"):
        resp = client.get("/api/videos/a-pdf:ee55", params={"platform": "odysee"})
    assert resp.status_code == 415
    assert "document" in resp.json()["detail"]


def test_odysee_details_proxy_down_falls_back_to_ytdlp(client):
    """Proxy unreachable: the yt-dlp path still answers."""
    details = VideoDetails(video_id="aa11", title="Linux Video", duration=120)
    post_patch = patch.object(
        httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ConnectError("down"))
    )
    ytdlp_patch = patch.object(ytdlp, "video_details", AsyncMock(return_value=details))
    with post_patch, ytdlp_patch:
        resp = client.get("/api/videos/linux-video:aa11", params={"platform": "odysee"})
    assert resp.status_code == 200
    # The requested 'name:claim' id is kept, not yt-dlp's bare claim id.
    assert resp.json()["video_id"] == "linux-video:aa11"


# ─── /api/videos/{id}/streams?platform=odysee ───

MASTER_URL = "https://cdn.example/abc/master.m3u8"
MASTER = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-STREAM-INF:BANDWIDTH=2890800,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2"
v0.m3u8

#EXT-X-STREAM-INF:BANDWIDTH=655600,RESOLUTION=640x360,CODECS="avc1.4d401e,mp4a.40.2"
v1.m3u8

#EXT-X-STREAM-INF:BANDWIDTH=215600,RESOLUTION=256x144,CODECS="avc1.4d400b,mp4a.40.2"
v2.m3u8
"""
# Top rung without an audio codec: picking it by height would play silent.
MASTER_AUDIO_GAP = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-STREAM-INF:BANDWIDTH=4500000,RESOLUTION=2560x1440,CODECS="avc1.640028"
v0.m3u8

#EXT-X-STREAM-INF:BANDWIDTH=655600,RESOLUTION=640x360,CODECS="avc1.4d401e,mp4a.40.2"
v1.m3u8
"""
# Every rung video-only: the ladder must be skipped for the original file.
MASTER_NO_AUDIO = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-STREAM-INF:BANDWIDTH=2890800,RESOLUTION=1280x720,CODECS="avc1.4d401f"
v0.m3u8

#EXT-X-STREAM-INF:BANDWIDTH=215600,RESOLUTION=256x144,CODECS="avc1.4d400b"
v2.m3u8
"""


def _fail_resolve():
    return patch.object(
        ytdlp,
        "resolve_streams",
        AsyncMock(
            side_effect=ytdlp.UpstreamError(
                "ERROR: [lbry] ee55: This stream is not live"
            )
        ),
    )


def test_odysee_streams_progressive(client):
    """Original file without transcoding: progressive, yt-dlp never called."""
    url = "https://cdn.example/original.mp4"
    proxy = _mock_proxy({"get": {"streaming_url": url}, "resolve": RESOLVE_RESULT})
    with proxy, _mock_head(url, "video/mp4"), _no_ytdlp("resolve_streams"):
        resp = client.get(
            "/api/videos/linux-video:aa11/streams", params={"platform": "odysee"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "progressive"
    assert body["url"] == url
    assert body["title"] == "Linux Video"
    assert body["duration"] == 120
    assert body["height"] is None


def test_odysee_streams_hls_default_cap(client):
    """Transcoded video: the best variant under the default cap (720p here)."""
    proxy = _mock_proxy(
        {"get": {"streaming_url": "https://cdn.example/abc/short.mp4"}, "resolve": RESOLVE_RESULT}
    )
    get_patch = _mock_master_get()
    with proxy, _mock_head(MASTER_URL, "application/x-mpegurl"), get_patch, _no_ytdlp("resolve_streams"):
        resp = client.get(
            "/api/videos/linux-video:aa11/streams", params={"platform": "odysee"}
        )
    body = resp.json()
    assert body["kind"] == "hls"
    assert body["url"] == "https://cdn.example/abc/v0.m3u8"
    assert body["height"] == 720


def test_odysee_streams_hls_skips_silent_top_variant(client):
    """A video-only rung must lose to a lower rung that carries audio."""
    url = "https://cdn.example/abc/short.mp4"
    proxy = _mock_proxy({"get": {"streaming_url": url}, "resolve": RESOLVE_RESULT})
    get_patch = _mock_master_get(MASTER_AUDIO_GAP)
    with proxy, _mock_head(MASTER_URL, "application/x-mpegurl"), get_patch, _no_ytdlp("resolve_streams"):
        resp = client.get(
            "/api/videos/linux-video:aa11/streams", params={"platform": "odysee"}
        )
    body = resp.json()
    assert body["kind"] == "hls"
    assert body["url"] == "https://cdn.example/abc/v1.m3u8"
    assert body["height"] == 360


def test_odysee_streams_all_silent_ladder_falls_back_to_original(client):
    """A ladder with no audio anywhere plays the original file, not silence."""
    url = "https://cdn.example/abc/short.mp4"
    proxy = _mock_proxy({"get": {"streaming_url": url}, "resolve": RESOLVE_RESULT})
    get_patch = _mock_master_get(MASTER_NO_AUDIO)
    with proxy, _mock_head(MASTER_URL, "application/x-mpegurl"), get_patch, _no_ytdlp("resolve_streams"):
        resp = client.get(
            "/api/videos/linux-video:aa11/streams", params={"platform": "odysee"}
        )
    body = resp.json()
    assert body["kind"] == "progressive"
    assert body["url"] == url
    assert body["height"] is None


def test_odysee_streams_hls_respects_max_height(client):
    proxy = _mock_proxy(
        {"get": {"streaming_url": "https://cdn.example/abc/short.mp4"}, "resolve": RESOLVE_RESULT}
    )
    get_patch = _mock_master_get()
    with proxy, _mock_head(MASTER_URL, "application/x-mpegurl"), get_patch, _no_ytdlp("resolve_streams"):
        resp = client.get(
            "/api/videos/linux-video:aa11/streams",
            params={"platform": "odysee", "max_height": 480},
        )
    body = resp.json()
    assert body["kind"] == "hls"
    assert body["url"] == "https://cdn.example/abc/v1.m3u8"
    assert body["height"] == 360


def test_odysee_streams_hls_cap_at_lowest_variant(client):
    """A cap equal to the lowest variant serves that variant exactly."""
    proxy = _mock_proxy(
        {"get": {"streaming_url": "https://cdn.example/abc/short.mp4"}, "resolve": RESOLVE_RESULT}
    )
    get_patch = _mock_master_get()
    with proxy, _mock_head(MASTER_URL, "application/x-mpegurl"), get_patch, _no_ytdlp("resolve_streams"):
        resp = client.get(
            "/api/videos/linux-video:aa11/streams",
            params={"platform": "odysee", "max_height": 144},
        )
    body = resp.json()
    assert body["kind"] == "hls"
    assert body["url"] == "https://cdn.example/abc/v2.m3u8"
    assert body["height"] == 144


def _mock_master_get(master: str = MASTER):
    async def fake_get(url, **kwargs):
        assert str(url) == MASTER_URL
        return httpx.Response(
            200, text=master, request=httpx.Request("GET", str(url))
        )

    return patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=fake_get))


def test_odysee_streams_cached_across_calls(client):
    """The resolution is cached: a second call never touches the proxy."""
    url = "https://cdn.example/original.mp4"
    proxy = _mock_proxy({"get": {"streaming_url": url}, "resolve": RESOLVE_RESULT})
    with proxy, _mock_head(url, "video/mp4"):
        first = client.get(
            "/api/videos/linux-video:aa11/streams", params={"platform": "odysee"}
        )
    post_patch = patch.object(
        httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ConnectError("down"))
    )
    with post_patch:
        second = client.get(
            "/api/videos/linux-video:aa11/streams", params={"platform": "odysee"}
        )
    assert first.json() == second.json()


def test_odysee_streams_non_media_claim(client):
    """A stale document/image entry must say so, not 'this stream is not live'."""
    proxy = _mock_proxy({"get": {}, "resolve": RESOLVE_RESULT})
    with proxy, _no_ytdlp("resolve_streams"):
        resp = client.get(
            "/api/videos/a-pdf:ee55/streams", params={"platform": "odysee"}
        )
    assert resp.status_code == 415
    assert "document" in resp.json()["detail"]


def test_odysee_streams_no_streaming_url_falls_back_to_ytdlp(client):
    """Proxy has no streaming URL for a playable claim: yt-dlp answers."""
    proxy = _mock_proxy({"get": {}, "resolve": RESOLVE_RESULT})
    fake = AsyncMock(
        return_value=StreamInfo(
            kind="hls", url="https://fallback/x.m3u8", title="Linux Video", duration=120, height=720
        )
    )
    head_patch = patch.object(
        httpx.AsyncClient, "head", AsyncMock(side_effect=AssertionError("no HEAD expected"))
    )
    with proxy, head_patch, patch.object(ytdlp, "resolve_streams", fake):
        resp = client.get(
            "/api/videos/linux-video:aa11/streams", params={"platform": "odysee"}
        )
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://fallback/x.m3u8"
    fake.assert_awaited_once()


def test_odysee_streams_video_claim_keeps_upstream_error(client):
    """Proxy without streaming URL and yt-dlp failing keeps the 502."""
    proxy = _mock_proxy({"get": {}, "resolve": RESOLVE_RESULT})
    with proxy, _fail_resolve():
        resp = client.get(
            "/api/videos/other-video:bb22/streams", params={"platform": "odysee"}
        )
    assert resp.status_code == 502


def test_pick_hls_variant_no_fit_degrades_to_lowest():
    url, height = odysee._pick_hls_variant(MASTER, MASTER_URL, max_height=100)
    assert url == "https://cdn.example/abc/v2.m3u8"
    assert height == 144


def test_pick_hls_variant_requires_audio():
    url, height = odysee._pick_hls_variant(MASTER_AUDIO_GAP, MASTER_URL, max_height=1440)
    assert url == "https://cdn.example/abc/v1.m3u8"
    assert height == 360


def test_pick_hls_variant_no_audio_returns_none():
    url, height = odysee._pick_hls_variant(MASTER_NO_AUDIO, MASTER_URL, max_height=1440)
    assert url is None
    assert height is None


def test_pick_hls_variant_unknown_codecs_counts_as_muxed():
    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100000,RESOLUTION=640x360\nv0.m3u8\n"
    url, height = odysee._pick_hls_variant(master, MASTER_URL, max_height=1440)
    assert url == "https://cdn.example/abc/v0.m3u8"
    assert height == 360


def test_pick_hls_variant_bare_media_playlist_unchanged():
    media = "#EXTM3U\n#EXTINF:10,\nseg0.ts\n#EXT-X-ENDLIST\n"
    url, height = odysee._pick_hls_variant(media, MASTER_URL, max_height=1440)
    assert url == MASTER_URL
    assert height is None


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


def test_odysee_comments_cursor_pagination(client):
    # A full page means more may follow: next_cursor carries the next page
    # number, and passing it back as `cursor` requests that page.
    post_patch, _ = _mock_odysee_http(
        [_rpc(COMMENT_LIST_RESULT), _rpc(REACTION_LIST_RESULT)]
    )
    with post_patch as post_mock:
        resp = client.get(
            "/api/videos/linux-video:aa11/comments",
            params={"platform": "odysee", "page_size": 2, "cursor": "3"},
        )
    assert resp.status_code == 200
    assert resp.json()["next_cursor"] == "4"
    assert post_mock.call_args_list[0].kwargs["json"]["params"]["page"] == 3


def test_comments_youtube_requires_account(client):
    # YouTube comments come from the signed-in account: no token pushed here.
    resp = client.get(
        "/api/videos/vid000000001/comments", params={"platform": "youtube"}
    )
    assert resp.status_code == 409


def test_comments_default_platform_is_youtube(client):
    resp = client.get("/api/videos/vid000000001/comments")
    assert resp.status_code == 409


def test_comments_unsupported_platform(client):
    resp = client.get(
        "/api/videos/vid000000001/comments", params={"platform": "bitchute"}
    )
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
    assert resp.json() == {
        "items": [],
        "total": 0,
        "next_cursor": None,
        "disabled": True,
    }


def test_odysee_replies_requests_parent_only(client):
    """`parent_id` replaces `top_level`, else Commentron returns the whole thread."""
    seen: list[dict] = []
    results = iter([_rpc(COMMENT_LIST_RESULT), _rpc(REACTION_LIST_RESULT)])

    async def fake_post(url, **kwargs):
        seen.append(kwargs["json"]["params"])
        return httpx.Response(
            200, json=next(results), request=httpx.Request("POST", str(url))
        )

    with patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=fake_post)):
        resp = client.get(
            "/api/videos/linux-video:aa11/comments/cid1/replies",
            params={"platform": "odysee"},
        )
    assert resp.status_code == 200
    assert seen[0]["parent_id"] == "cid1"
    assert "top_level" not in seen[0]
    assert resp.json()["items"][0]["text"] == "Great video"


def test_odysee_replies_cursor_pagination(client):
    post_patch, _ = _mock_odysee_http(
        [_rpc(COMMENT_LIST_RESULT), _rpc(REACTION_LIST_RESULT)]
    )
    with post_patch as post_mock:
        resp = client.get(
            "/api/videos/linux-video:aa11/comments/cid1/replies",
            params={"platform": "odysee", "page_size": 2, "cursor": "3"},
        )
    assert resp.status_code == 200
    assert resp.json()["next_cursor"] == "4"
    assert post_mock.call_args_list[0].kwargs["json"]["params"]["page"] == 3


def test_replies_unsupported_platform(client):
    resp = client.get(
        "/api/videos/vid000000001/comments/cid1/replies", params={"platform": "bitchute"}
    )
    assert resp.status_code == 501


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
    assert video_id_from_url("https://odysee.com/@chan:cc33/my-video:ab12") == (
        "my-video:ab12",
        "odysee",
    )
    assert video_id_from_url("https://odysee.com/my-video:ab12") == (
        "my-video:ab12",
        "odysee",
    )
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
