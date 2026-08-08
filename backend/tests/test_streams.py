"""Search, listings, details and stream resolution with yt-dlp mocked."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from ytui_server.services import ytdlp


def _info(formats, **extra):
    return {"title": "A video", "duration": 212, "formats": formats, **extra}


class FakeYDL:
    def __init__(self, info):
        self._info = info

    def __call__(self, opts):
        self._opts = opts
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        if isinstance(self._info, Exception):
            raise self._info
        return self._info


def _patch_ydl(info):
    import yt_dlp

    return patch.object(yt_dlp, "YoutubeDL", FakeYDL(info))


# ─── /api/search ───


def test_search(client):
    entries = [
        {"id": "vid000000001", "title": "Hit", "channel": "Chan", "duration": 60},
        {"id": "PLxyz", "title": "A playlist", "_type": "playlist",
         "url": "https://www.youtube.com/playlist?list=PLxyz"},
    ]
    with _patch_ydl({"entries": entries}):
        resp = client.get("/api/search", params={"q": "hit"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["kind"] for i in items] == ["video", "playlist"]


def test_search_upstream_error(client):
    with _patch_ydl(RuntimeError("boom")):
        resp = client.get("/api/search", params={"q": "hit"})
    assert resp.status_code == 502


def test_search_requires_query(client):
    assert client.get("/api/search").status_code == 422


# ─── /api/channels/{id}/videos & /api/ytplaylists/{id}/videos ───


def test_channel_videos(client):
    entries = [{"id": "vid000000001", "title": "V1", "channel": "Chan"}]
    with _patch_ydl({"entries": entries, "title": "Chan"}):
        resp = client.get("/api/channels/UCtest000000000000000000/videos")
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel"]["channel_id"] == "UCtest000000000000000000"
    assert body["items"][0]["video_id"] == "vid000000001"


def test_channel_videos_offset_window(client):
    """offset/limit map onto a yt-dlp item range, with one extra item probed."""
    entries = [{"id": f"vid00000000{i}", "title": f"V{i}"} for i in range(4)]
    ydl = FakeYDL({"entries": entries, "title": "Chan"})
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl):
        resp = client.get(
            "/api/channels/UCtest000000000000000000/videos",
            params={"limit": 3, "offset": 10},
        )
    assert ydl._opts["playlist_items"] == "11:14"
    body = resp.json()
    assert [i["video_id"] for i in body["items"]] == ["vid000000000", "vid000000001", "vid000000002"]
    assert body["has_more"] is True


def test_channel_videos_exhausted(client):
    """A short page means no further videos: clients must stop asking."""
    entries = [{"id": "vid000000001", "title": "V1"}]
    with _patch_ydl({"entries": entries, "title": "Chan"}):
        resp = client.get(
            "/api/channels/UCtest000000000000000000/videos", params={"limit": 3}
        )
    assert resp.json()["has_more"] is False


def test_playlist_videos(client):
    entries = [{"id": "vid000000001", "title": "V1"}]
    with _patch_ydl({"entries": entries, "title": "My List"}):
        resp = client.get("/api/ytplaylists/PLxyz/videos")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "My List"
    assert len(body["items"]) == 1


# ─── /api/videos/{id} details ───


def test_video_details(client):
    info = {
        "id": "vid000000001",
        "title": "Full",
        "description": "desc",
        "view_count": 100,
        "like_count": 5,
        "duration": 212,
        "upload_date": "20240602",
        "channel_id": "UCx",
        "channel": "Chan",
    }
    with _patch_ydl(info):
        resp = client.get("/api/videos/vid000000001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["upload_date"] == "2024-06-02"
    assert body["view_count"] == 100


# ─── /api/videos/{id}/streams ───

HLS = {"format_id": "hls", "url": "https://x/hls.m3u8", "protocol": "m3u8_native",
       "vcodec": "avc1", "acodec": "mp4a", "height": 720}
PROG = {"format_id": "22", "url": "https://x/prog.mp4", "protocol": "https",
        "vcodec": "avc1", "acodec": "mp4a", "height": 720}
VONLY = {"format_id": "299", "url": "https://x/video.mp4", "protocol": "https",
         "vcodec": "avc1", "acodec": "none", "height": 1080, "tbr": 4000}
AONLY = {"format_id": "140", "url": "https://x/audio.m4a", "protocol": "https",
         "vcodec": "none", "acodec": "mp4a", "abr": 128}


def test_streams_prefers_hls(client):
    with _patch_ydl(_info([PROG, HLS], manifest_url="https://x/master.m3u8")):
        resp = client.get("/api/videos/vid000000001/streams")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "hls"
    assert body["url"] == "https://x/master.m3u8"
    assert body["duration"] == 212


def test_streams_progressive_fallback(client):
    with _patch_ydl(_info([PROG, VONLY, AONLY])):
        resp = client.get("/api/videos/vid000000001/streams")
    body = resp.json()
    assert body["kind"] == "progressive"
    assert body["url"] == "https://x/prog.mp4"


def test_streams_split_dash(client):
    with _patch_ydl(_info([VONLY, AONLY])):
        resp = client.get("/api/videos/vid000000001/streams")
    body = resp.json()
    assert body["kind"] == "split"
    assert body["video_url"] == "https://x/video.mp4"
    assert body["audio_url"] == "https://x/audio.m4a"


def test_streams_audio_only(client):
    with _patch_ydl(_info([PROG, AONLY])):
        resp = client.get(
            "/api/videos/vid000000001/streams", params={"audio_only": True}
        )
    body = resp.json()
    assert body["url"] == "https://x/audio.m4a"


def test_streams_max_height_falls_back_to_lowest(client):
    with _patch_ydl(_info([VONLY, AONLY])):
        resp = client.get(
            "/api/videos/vid000000001/streams", params={"max_height": 720}
        )
    # Only a 1080p video track exists: a cap degrades, it never fails playback.
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "split"
    assert body["height"] == 1080


def test_streams_hls_variant_when_capped(client):
    hls720 = dict(HLS, url="https://x/720.m3u8", height=720)
    hls1080 = dict(HLS, url="https://x/1080.m3u8", height=1080)
    info = _info([hls720, hls1080], manifest_url="https://x/master.m3u8")
    with _patch_ydl(info):
        resp = client.get("/api/videos/vid000000001/streams")
    body = resp.json()
    assert (body["url"], body["height"]) == ("https://x/master.m3u8", 1080)
    with _patch_ydl(info):
        resp = client.get(
            "/api/videos/vid000000001/streams", params={"max_height": 720}
        )
    body = resp.json()
    # The master carries every variant: capping hands over one variant playlist.
    assert (body["url"], body["height"]) == ("https://x/720.m3u8", 720)


def test_streams_reports_height(client):
    with _patch_ydl(_info([PROG])):
        resp = client.get("/api/videos/vid000000001/streams")
    assert resp.json()["height"] == 720


def test_streams_expiry_parsed(client):
    prog = dict(PROG, url="https://x/prog.mp4?expire=1720000000")
    with _patch_ydl(_info([prog])):
        resp = client.get("/api/videos/vid000000001/streams")
    assert resp.json()["expires_at"] == "2024-07-03T09:46:40Z"


# ─── BitChute seed hosts ───

BC_URL = "https://seed132.bitchute.com/Ynl2NN4t7THl/pCkE9k5o6DUE.mp4"
BC_PROG = {"format_id": "0", "url": BC_URL, "protocol": "https",
           "vcodec": None, "acodec": None}


def _patch_head(by_url):
    """Fake httpx.Client whose HEAD answers per URL: (status, content-type)."""
    seen: list[str] = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def head(self, url):
            seen.append(url)
            outcome = by_url.get(url, (404, "text/html"))
            if isinstance(outcome, Exception):
                raise outcome
            status, ctype = outcome
            return httpx.Response(status, headers={"content-type": ctype})

    return patch.object(ytdlp.httpx, "Client", FakeClient), seen


def test_streams_bitchute_keeps_canonical_seed(client):
    head, seen = _patch_head({BC_URL: (200, "video/mp4")})
    with _patch_ydl(_info([BC_PROG], url=BC_URL)), head:
        resp = client.get("/api/videos/pCkE9k5o6DUE/streams", params={"platform": "bitchute"})
    assert resp.json()["url"] == BC_URL
    assert seen == [BC_URL]


def test_streams_bitchute_skips_seed_serving_html(client):
    """A seed answering 200 text/html is a Cloudflare page, not the video."""
    good = BC_URL.replace("seed132", "seed150")
    head, seen = _patch_head(
        {BC_URL: httpx.ConnectError("aborted"), good: (200, "video/mp4")}
    )
    with _patch_ydl(_info([BC_PROG], url=BC_URL)), head:
        resp = client.get("/api/videos/pCkE9k5o6DUE/streams", params={"platform": "bitchute"})
    assert resp.json()["url"] == good
    # The canonical host is retried before any alternate is considered.
    assert seen[:2] == [BC_URL, BC_URL]
    assert BC_URL.replace("seed132", "seed122") in seen


def test_streams_bitchute_falls_back_to_canonical_when_no_seed_serves(client):
    head, _ = _patch_head({})
    with _patch_ydl(_info([BC_PROG], url=BC_URL)), head:
        resp = client.get("/api/videos/pCkE9k5o6DUE/streams", params={"platform": "bitchute"})
    assert resp.json()["url"] == BC_URL


def test_streams_none_found(client):
    with _patch_ydl(_info([])):
        resp = client.get("/api/videos/vid000000001/streams")
    assert resp.status_code == 502


def test_streams_subtitles_manual_and_auto(client):
    info = _info(
        [PROG],
        subtitles={"fr": [{"ext": "vtt", "url": "u1", "name": "French"}],
                   "live_chat": [{"ext": "json", "url": "chat"}]},
        automatic_captions={"en": [{"ext": "vtt", "url": "u2"}],
                            "de": [{"ext": "vtt", "url": "u3"}]},
    )
    with _patch_ydl(info):
        resp = client.get(
            "/api/videos/vid000000001/streams", params={"sub_langs": "en"}
        )
    subs = resp.json()["subtitles"]
    assert [(s["lang"], s["label"], s["url"], s["auto"]) for s in subs] == [
        ("fr", "French", "u1", False),
        ("en", "en (auto)", "u2", True),
    ]


def test_streams_subtitles_default_manual_only(client):
    info = _info(
        [PROG],
        subtitles={"fr": [{"ext": "vtt", "url": "u1", "name": "French"}]},
        automatic_captions={"en": [{"ext": "vtt", "url": "u2"}]},
    )
    with _patch_ydl(info):
        resp = client.get("/api/videos/vid000000001/streams")
    assert [s["lang"] for s in resp.json()["subtitles"]] == ["fr"]


def test_streams_subtitles_absent_in_audio_only(client):
    info = _info(
        [PROG, AONLY],
        subtitles={"fr": [{"ext": "vtt", "url": "u1", "name": "French"}]},
    )
    with _patch_ydl(info):
        resp = client.get(
            "/api/videos/vid000000001/streams",
            params={"audio_only": True, "sub_langs": "fr"},
        )
    assert resp.json()["subtitles"] == []


# ─── entry_to_item unit tests (ported) ───


@pytest.mark.parametrize(
    "entry, kind",
    [
        ({"id": "vid000000001", "title": "V"}, "video"),
        ({"id": "PLabc", "title": "P", "_type": "playlist",
          "url": "https://www.youtube.com/playlist?list=PLabc"}, "playlist"),
        ({"id": "UCabcdefghijklmnopqrstuv", "title": "C", "ie_key": "YoutubeTab",
          "url": "https://www.youtube.com/@chan"}, "channel"),
    ],
)
def test_entry_to_item_kinds(entry, kind):
    item = ytdlp.entry_to_item(entry)
    assert item is not None
    assert item.kind == kind


def test_entry_to_item_no_id():
    assert ytdlp.entry_to_item({"title": "nope"}) is None


# ─── /api/videos/{id}/related ───


def _make_lockup(content_id="rel0000001", title="Related video", channel="Chan Related",
                  duration_text="4:20", thumb_url="https://i.ytimg.com/vi/rel0000001/hq.jpg"):
    return {
        "lockupViewModel": {
            "contentId": content_id,
            "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "metadata": {
                "lockupMetadataViewModel": {
                    "title": {"content": title},
                    "metadata": {
                        "contentMetadataViewModel": {
                            "metadataRows": [
                                {"metadataParts": [{"text": {"content": channel}}]}
                            ]
                        }
                    },
                }
            },
            "contentImage": {
                "thumbnailViewModel": {
                    "image": {"sources": [{"url": thumb_url, "width": 360}]},
                    "overlays": [
                        {
                            "thumbnailOverlayBadgeViewModel": {
                                "thumbnailBadges": [
                                    {"thumbnailBadgeViewModel": {"text": duration_text}}
                                ]
                            }
                        }
                    ],
                }
            },
        }
    }


def _yt_initial_data_html(lockups):
    data = {"contents": {"items": lockups}}
    return f"<html><script>var ytInitialData = {json.dumps(data)};</script></html>"


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        return None


class FakeAsyncClient:
    def __init__(self, response=None, error=None, **kwargs):
        self._response = response
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        if self._error is not None:
            raise self._error
        return self._response


def test_related_videos(client):
    html = _yt_initial_data_html([_make_lockup()])
    fake_client = FakeAsyncClient(response=FakeResponse(html))
    with patch.object(httpx, "AsyncClient", lambda **kw: fake_client):
        resp = client.get("/api/videos/vid000000001/related")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["video_id"] == "rel0000001"
    assert items[0]["title"] == "Related video"
    assert items[0]["channel_title"] == "Chan Related"
    assert items[0]["duration"] == 260


def test_related_videos_rate_limited(client):
    fake_client = FakeAsyncClient(response=FakeResponse("", status_code=429))
    with patch.object(httpx, "AsyncClient", lambda **kw: fake_client):
        resp = client.get("/api/videos/vid000000001/related")
    assert resp.status_code == 502
    assert "rate-limited" in resp.json()["detail"]


def test_related_videos_non_youtube_returns_empty(client):
    resp = client.get("/api/videos/vid000000001/related", params={"platform": "bitchute"})
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_related_videos_network_error(client):
    fake_client = FakeAsyncClient(error=httpx.RequestError("boom"))
    with patch.object(httpx, "AsyncClient", lambda **kw: fake_client):
        resp = client.get("/api/videos/vid000000001/related")
    assert resp.status_code == 502
