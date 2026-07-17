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


def test_streams_max_height_filters(client):
    with _patch_ydl(_info([VONLY, AONLY])):
        resp = client.get(
            "/api/videos/vid000000001/streams", params={"max_height": 720}
        )
    # 1080p video-only excluded -> no video track fits, no progressive: 502
    assert resp.status_code == 502


def test_streams_expiry_parsed(client):
    prog = dict(PROG, url="https://x/prog.mp4?expire=1720000000")
    with _patch_ydl(_info([prog])):
        resp = client.get("/api/videos/vid000000001/streams")
    assert resp.json()["expires_at"] == "2024-07-03T09:46:40Z"


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
