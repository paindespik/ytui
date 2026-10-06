"""CrowdBunker listings, searches, details, feed, refs and URL mapping (HTTP mocked)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from ytui_server.models import Video, VideoDetails, video_id_from_url
from ytui_server.services import crowdbunker, ytdlp

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


def _hit(document: dict) -> dict:
    """Wrap a post document in the Typesense hit envelope of the search API."""
    return {"document": document, "highlights": [], "text_match": 1}


SEARCH_PAGE_1 = {
    "found": 17,
    "page": 1,
    "search_cutoff": False,
    "hits": [
        _hit(
            {
                "mediaType": "video",
                "uid": "svid00001",
                "title": "Found video one",
                "channelName": ORG_NAME,
                "channelUid": ORG,
                "publishedAtTimestamp": 1743200000,
                "videoDuration": 120,
                "thumbnailUrl": "https://img.crowdbunker.com/s/1.jpg",
                # the search index serializes some counters as strings
                "viewCount": "1262",
            }
        ),
        # Channel hits are kept (they open the channel), text hits dropped
        _hit(
            {
                "mediaType": "channel",
                "uid": "schan001",
                "channelUid": "schan001",
                "name": "Some channel",
                "thumbnailUrl": "https://img.crowdbunker.com/o/default.jpg",
                "subscribersCount": 3,
            }
        ),
        _hit({"mediaType": "text", "uid": "stext001", "title": "A text post"}),
        _hit(
            {
                "mediaType": "video",
                "uid": "svid00003",
                "title": "Found video two",
                "channelName": "Org B",
                "channelUid": "OrgB",
                "publishedAtTimestamp": None,
                "videoDuration": None,
            }
        ),
        # Filler text hits: a full page carries SEARCH_PAGE_SIZE mixed-type hits
        *(
            _hit({"mediaType": "text", "uid": f"filler{i:02d}", "title": f"filler {i}"})
            for i in range(crowdbunker.SEARCH_PAGE_SIZE - 4)
        ),
    ],
}

SEARCH_PAGE_2 = {
    "found": 17,
    "page": 2,
    "search_cutoff": False,
    "hits": [
        _hit(
            {
                "mediaType": "video",
                "uid": "svid00005",
                "title": "Found video three",
                "channelName": ORG_NAME,
                "channelUid": ORG,
                "publishedAtTimestamp": 1743100000,
                "videoDuration": 30,
                "thumbnailUrl": "",
            }
        ),
        _hit({"mediaType": "text", "uid": "stext002", "title": "more text"}),
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


def _route_cb_http(routes: dict):
    """Patch httpx.AsyncClient.get to answer by URL suffix.

    A dict payload is served as is, a list as successive pages (the last one
    repeats), an int as an error status. Records every (url, params).
    """

    def fake_get(url, **kwargs):
        url = str(url)
        calls.append((url, dict(kwargs.get("params") or {})))
        for suffix, payload in routes.items():
            if not url.endswith(suffix):
                continue
            request = httpx.Request("GET", url)
            if isinstance(payload, int):
                return httpx.Response(payload, json={"detail": "x"}, request=request)
            if isinstance(payload, list):
                served = sum(1 for u, _ in calls if u.endswith(suffix)) - 1
                payload = payload[min(served, len(payload) - 1)]
            return httpx.Response(200, json=payload, request=request)
        raise AssertionError(f"unexpected GET {url}")

    calls: list[tuple[str, dict]] = []
    return patch.object(httpx.AsyncClient, "get", side_effect=fake_get), calls


@pytest.fixture(autouse=True)
def _fresh_popular_cache():
    crowdbunker._popular_cache.clear()
    yield
    crowdbunker._popular_cache.clear()


# ─── URL / id mapping ───


def test_video_url_and_id_roundtrip():
    video = Video(video_id="vid000001", title="t", platform="crowdbunker")
    assert video.url == "https://crowdbunker.com/v/vid000001"
    assert video_id_from_url("https://crowdbunker.com/v/vid000001") == (
        "vid000001",
        "crowdbunker",
    )
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
    """One-character queries (400 upstream) fall back to the local title scan."""
    get_patch, calls = _route_cb_http({f"/organization/{ORG}/posts": [POSTS_PAGE_1, POSTS_PAGE_2]})
    with get_patch:
        resp = client.get(
            f"/api/channels/{ORG}/videos",
            params={"platform": "crowdbunker", "q": "S"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert [i["video_id"] for i in body["items"]] == ["vid000001", "vid00003"]
    assert not any(url.endswith("/search") for url, _ in calls)


def _doc(uid: str, title: str, views=None, media: str = "video", channel: str = ORG) -> dict:
    """A post in the flat search-index shape (searches, channel shelves)."""
    return {
        "mediaType": media,
        "uid": uid,
        "title": title,
        "channelName": ORG_NAME,
        "channelUid": channel,
        "publishedAtTimestamp": "1725229740",
        "videoDuration": "4686",
        "viewCount": views,
        "thumbnailUrl": f"https://img.crowdbunker.com/{uid}/standard.jpg",
    }


ORG_SEARCH_PAGES = [
    {
        "posts": [
            _doc("cvid00001", "La clé n°1", "4228"),
            _doc("ctxt00001", "", media="text"),
            _doc("cvid00002", "La clé n°2"),
        ],
        "hasMore": True,
        "page": 1,
        "total": 4,
    },
    {"posts": [_doc("cvid00003", "La clé n°3", 12)], "hasMore": False, "page": 2, "total": 4},
]


def test_crowdbunker_channel_search_uses_channel_search_api(client):
    """The channel page's own search covers the whole channel, not only the
    newest uploads a title scan would see; text posts are dropped."""
    get_patch, calls = _route_cb_http({f"/organization/{ORG}/search": ORG_SEARCH_PAGES})
    with get_patch:
        resp = client.get(
            f"/api/channels/{ORG}/videos",
            params={"platform": "crowdbunker", "q": "la clé"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert [i["video_id"] for i in body["items"]] == ["cvid00001", "cvid00002", "cvid00003"]
    assert body["has_more"] is False
    first = body["items"][0]
    assert first["channel_id"] == ORG and first["view_count"] == 4228
    assert first["duration"] == 4686 and first["published"]
    assert [p for _, p in calls] == [{"q": "la clé", "page": 1}, {"q": "la clé", "page": 2}]


def test_crowdbunker_channel_search_offset_counts_videos(client):
    get_patch, _ = _route_cb_http({f"/organization/{ORG}/search": ORG_SEARCH_PAGES})
    with get_patch:
        resp = client.get(
            f"/api/channels/{ORG}/videos",
            params={"platform": "crowdbunker", "q": "la clé", "limit": 1, "offset": 1},
        )
    body = resp.json()
    assert [i["video_id"] for i in body["items"]] == ["cvid00002"]
    assert body["has_more"] is True


def test_crowdbunker_channel_search_unknown_channel(client):
    get_patch, _ = _route_cb_http({"/organization/nobody/search": 404})
    with get_patch:
        resp = client.get(
            "/api/channels/nobody/videos", params={"platform": "crowdbunker", "q": "clé"}
        )
    assert resp.status_code == 502


# ─── most viewed listing (sort=views) ───


def _post(uid: str, title: str, views) -> dict:
    return {
        "uid": uid,
        "video": {"title": title, "duration": 60, "censored": False, "viewCount": views},
        "publishedAt": "2026-03-28T13:12:41+01:00",
        "organization": {"uid": ORG, "name": ORG_NAME},
    }


POPULAR_POSTS = {
    "last": None,
    "items": [
        _post("pvid00001", "Newest, few views", 10),
        _post("pvid00002", "Middle", 500),
        _post("pvid00003", "Views unknown", None),
        _post("pvid00004", "Top recent", 900),
    ],
}

SECTIONS = {
    "featured": None,
    "sections": [
        # The all-time top: an old video the scan of recent posts misses.
        {"id": "most-viewed", "posts": [_doc("pvid00004", "Top recent", 900),
                                        _doc("pold00001", "Old classic", 5000)]},
        {"id": "rushs", "posts": [_doc("prush0001", "A rush", 99999)]},
    ],
}


def test_crowdbunker_videos_by_views(client):
    get_patch, calls = _route_cb_http(
        {f"/organization/{ORG}/posts": POPULAR_POSTS, f"/organization/{ORG}/sections": SECTIONS}
    )
    with get_patch:
        first = client.get(
            f"/api/channels/{ORG}/videos",
            params={"platform": "crowdbunker", "sort": "views", "limit": 2},
        ).json()
        fetched = len(calls)
        second = client.get(
            f"/api/channels/{ORG}/videos",
            params={"platform": "crowdbunker", "sort": "views", "limit": 2, "offset": 2},
        ).json()
    assert [i["video_id"] for i in first["items"]] == ["pold00001", "pvid00004"]
    assert first["has_more"] is True
    assert [i["video_id"] for i in second["items"]] == ["pvid00002", "pvid00001"]
    assert second["has_more"] is True
    # Later pages slice the cached ranking: no new upstream walk.
    assert len(calls) == fetched


def test_crowdbunker_videos_by_views_without_shelf(client):
    """The shelf is best effort: its failure leaves the ranked recent videos."""
    get_patch, _ = _route_cb_http(
        {f"/organization/{ORG}/posts": POPULAR_POSTS, f"/organization/{ORG}/sections": 500}
    )
    with get_patch:
        resp = client.get(
            f"/api/channels/{ORG}/videos", params={"platform": "crowdbunker", "sort": "views"}
        )
    assert resp.status_code == 200
    assert [i["video_id"] for i in resp.json()["items"]] == [
        "pvid00004", "pvid00002", "pvid00001", "pvid00003"
    ]


# ─── video details / related ───

DETAILS = {
    "uid": "of9ezUzNTB",
    "authorName": "author",
    # The API now leaves `channel` empty: the creator is the organization.
    "channel": None,
    "organization": {"uid": "wolvagri", "name": "michel lanceur alerte"},
    "publishedAt": "2026-09-30T08:42:43+02:00",
    "video": {
        "title": "Macron: retard",
        "description": "Une description",
        "duration": 1216,
        "viewCount": 1262,
        "publishedAt": "2026-09-30T08:42:43+02:00",
        "thumbnails": [
            {"name": "default", "url": "https://img.crowdbunker.com/v/default.jpg"},
            {"name": "high", "url": "https://img.crowdbunker.com/v/high.jpg"},
        ],
    },
}


def test_video_details_carry_the_channel(client):
    get_patch, _ = _route_cb_http({"/post/of9ezUzNTB/details": DETAILS})
    with get_patch:
        resp = client.get("/api/videos/of9ezUzNTB", params={"platform": "crowdbunker"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["video_id"] == "of9ezUzNTB"
    assert body["channel_id"] == "wolvagri"
    assert body["channel_title"] == "michel lanceur alerte"
    assert body["title"] == "Macron: retard"
    assert body["description"] == "Une description"
    assert body["view_count"] == 1262 and body["duration"] == 1216
    assert body["upload_date"] == "2026-09-30"
    assert body["thumbnail_url"] == "https://img.crowdbunker.com/v/high.jpg"


def test_video_details_fall_back_to_ytdlp(client):
    fallback = VideoDetails(video_id="of9ezUzNTB", title="From yt-dlp")
    get_patch, _ = _route_cb_http({"/post/of9ezUzNTB/details": 500})
    with get_patch, patch.object(ytdlp, "video_details", return_value=fallback) as ydl:
        resp = client.get("/api/videos/of9ezUzNTB", params={"platform": "crowdbunker"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "From yt-dlp"
    ydl.assert_awaited_once_with("https://crowdbunker.com/v/of9ezUzNTB")


def test_related_lists_the_creator_channel(client):
    channel_page = {
        "last": None,
        "items": [
            {**_post("of9ezUzNTB", "Macron: retard", 1262),
             "organization": {"uid": "wolvagri", "name": "michel lanceur alerte"}},
            {**_post("other0001", "Another one", 3),
             "organization": {"uid": "wolvagri", "name": "michel lanceur alerte"}},
        ],
    }
    get_patch, _ = _route_cb_http(
        {"/post/of9ezUzNTB/details": DETAILS, "/organization/wolvagri/posts": channel_page}
    )
    with get_patch:
        resp = client.get("/api/videos/of9ezUzNTB/related", params={"platform": "crowdbunker"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["video_id"] for i in items] == ["other0001"]
    assert items[0]["channel_id"] == "wolvagri"


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


# ─── global search ───


def test_search_maps_video_and_channel_hits():

    get_patch, _ = _mock_cb_http([SEARCH_PAGE_1, {"hits": []}])
    with get_patch:
        videos = asyncio.run(crowdbunker.search("found", limit=20))
    assert [v.video_id for v in videos] == ["svid00001", "schan001", "svid00003"]
    channel = videos[1]
    assert channel.kind == "channel" and channel.platform == "crowdbunker"
    assert channel.title == "Some channel" and channel.channel_id == "schan001"
    assert channel.url == "https://crowdbunker.com/@schan001"
    assert channel.thumbnail_url == "https://img.crowdbunker.com/o/default.jpg"
    videos = [v for v in videos if v.kind == "video"]
    first = videos[0]
    assert first.title == "Found video one"
    assert first.channel_title == ORG_NAME
    assert first.channel_id == ORG
    assert first.duration == 120
    assert first.thumbnail_url == "https://img.crowdbunker.com/s/1.jpg"
    assert first.platform == "crowdbunker"
    assert first.published is not None and first.published.utcoffset() is not None
    assert first.view_count == 1262
    assert videos[1].duration is None and videos[1].published is None


def test_search_asks_full_pages():
    get_patch, calls = _route_cb_http({"/search/posts": {"hits": []}})
    with get_patch:
        asyncio.run(crowdbunker.search("  found  ", limit=20))
    assert calls == [
        (
            f"{crowdbunker.API_BASE}/search/posts",
            {"q": "found", "page": 1, "perPage": crowdbunker.SEARCH_PAGE_SIZE},
        )
    ]


def test_search_short_query_skips_the_api():
    """The API answers 400 under two characters: no call, no results."""
    get_patch, calls = _mock_cb_http([SEARCH_PAGE_1])
    with get_patch:
        assert asyncio.run(crowdbunker.search("m", limit=20)) == []
    assert calls[0] == 0


def test_search_paginates_until_limit():

    get_patch, calls = _mock_cb_http([SEARCH_PAGE_1, SEARCH_PAGE_2])
    with get_patch:
        videos = asyncio.run(crowdbunker.search("found", limit=4))
    assert [v.video_id for v in videos] == ["svid00001", "schan001", "svid00003", "svid00005"]
    assert calls[0] == 2


def test_search_stops_on_short_page():

    get_patch, calls = _mock_cb_http([SEARCH_PAGE_1, SEARCH_PAGE_2])
    with get_patch:
        videos = asyncio.run(crowdbunker.search("found", limit=20))
    assert len(videos) == 4  # 2 videos + 1 channel, 1 video, then page 2 was short
    assert calls[0] == 2


def test_search_stops_on_server_cutoff():
    # A full page flagged search_cutoff=True must not trigger another fetch.
    page = dict(SEARCH_PAGE_1, search_cutoff=True)
    get_patch, calls = _mock_cb_http([page, SEARCH_PAGE_2])
    with get_patch:
        videos = asyncio.run(crowdbunker.search("found", limit=20))
    assert [v.video_id for v in videos] == ["svid00001", "schan001", "svid00003"]
    assert calls[0] == 1


def test_search_router_crowdbunker(client):
    get_patch, _ = _mock_cb_http([SEARCH_PAGE_1, SEARCH_PAGE_2])
    with get_patch:
        resp = client.get("/api/search", params={"q": "found", "source": "crowdbunker"})
    assert resp.status_code == 200
    assert [i["video_id"] for i in resp.json()["items"]] == [
        "svid00001",
        "schan001",
        "svid00003",
        "svid00005",
    ]


def test_search_router_crowdbunker_short_query(client):
    resp = client.get("/api/search", params={"q": "m", "source": "crowdbunker"})
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_search_router_crowdbunker_error(client):
    async def boom(url, **kwargs):
        raise httpx.RequestError("down")

    with patch.object(httpx.AsyncClient, "get", side_effect=boom):
        resp = client.get("/api/search", params={"q": "found", "source": "crowdbunker"})
    assert resp.status_code == 502


# ─── home feed ───


def test_feed_includes_crowdbunker(client):
    # Pages: 1) channel resolve, 2) feed page 1, 3) feed page 2 (cursor walk).
    get_patch, _ = _mock_cb_http([POSTS_PAGE_1, POSTS_PAGE_1, POSTS_PAGE_2])
    with get_patch:
        client.post("/api/channels", json={"ref": f"crowdbunker:{ORG}"})
        resp = client.get("/api/feed", params={"refresh": True})
    assert resp.status_code == 200
    ids = [v["video_id"] for v in resp.json()["videos"]]
    assert "vid000001" in ids and "vid00003" in ids
    # the second page is walked: the older video shows up
    assert "vid00005" in ids
    # text and censored posts stay out of the feed
    assert "txt00002" not in ids and "vid00004" not in ids


# ─── playback (yt-dlp extractor) ───


def test_ytdlp_extractor_uses_current_api_host():
    """yt-dlp's CrowdBunkerIE still targets the legacy host, which answers 403."""
    import yt_dlp
    from yt_dlp.extractor.crowdbunker import CrowdBunkerIE

    crowdbunker.patch_ytdlp_extractors()
    patched = CrowdBunkerIE.__dict__["_download_json"]
    crowdbunker.patch_ytdlp_extractors()  # idempotent: no double wrapping
    assert CrowdBunkerIE.__dict__["_download_json"] is patched

    requested: list[str] = []

    def fake_handle(self, url_or_request, video_id, *args, **kwargs):
        requested.append(url_or_request)
        return json.dumps({"video": {"title": "Pope video", "duration": 78}}), None

    with patch.object(CrowdBunkerIE, "_download_webpage_handle", fake_handle):
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.get_info_extractor("CrowdBunker").extract(
                "https://crowdbunker.com/v/14Gc7GY7C4"
            )
    assert requested == [f"{crowdbunker.API_BASE}/post/14Gc7GY7C4/details"]
    assert info["title"] == "Pope video"
