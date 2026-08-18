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


def test_channel_videos_query_hits_youtube_search_tab(client):
    """`q` searches inside the channel rather than filtering client-side."""
    entries = [{"id": "vid000000001", "title": "Linux", "channel": "Chan"}]

    class RecordingYDL(FakeYDL):
        def extract_info(self, url, download=False):
            self.url = url
            return super().extract_info(url, download)

    ydl = RecordingYDL({"entries": entries, "title": "Chan"})
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl):
        resp = client.get(
            "/api/channels/UCtest000000000000000000/videos",
            params={"q": "linux hits", "limit": 3, "offset": 10},
        )
    assert resp.status_code == 200
    assert ydl.url == (
        "https://www.youtube.com/channel/UCtest000000000000000000"
        "/search?query=linux+hits"
    )
    assert ydl._opts["playlist_items"] == "11:14"
    assert [i["video_id"] for i in resp.json()["items"]] == ["vid000000001"]


def test_channel_videos_query_filters_platforms_without_search(client):
    """BitChute has no in-channel search: the listing is filtered on title."""
    entries = [
        {"id": "aaaaaaaaaaaa", "title": "Cooking pasta"},
        {"id": "bbbbbbbbbbbb", "title": "PASTA machine review"},
        {"id": "cccccccccccc", "title": "Bread"},
    ]
    with _patch_ydl({"entries": entries, "title": "Chan"}):
        resp = client.get(
            "/api/channels/chan0000/videos",
            params={"platform": "bitchute", "q": "pasta"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert [i["video_id"] for i in body["items"]] == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"]
    assert body["has_more"] is False


def test_channel_videos_rejects_empty_query(client):
    resp = client.get("/api/channels/UCtest000000000000000000/videos", params={"q": ""})
    assert resp.status_code == 422


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


def test_streams_split_beats_lower_progressive(client):
    # YouTube only muxes up to 360p: a muxed format must not cap the video below
    # the separate video/audio ladder just by being checked first.
    with _patch_ydl(_info([PROG, VONLY, AONLY])):
        resp = client.get("/api/videos/vid000000001/streams")
    body = resp.json()
    assert body["kind"] == "split"
    assert body["video_url"] == "https://x/video.mp4"
    assert body["height"] == 1080


def test_streams_progressive_wins_tie(client):
    # Same height on both paths: one muxed file, no external audio to sync.
    tie = {**VONLY, "height": 720}
    with _patch_ydl(_info([PROG, tie, AONLY])):
        resp = client.get("/api/videos/vid000000001/streams")
    body = resp.json()
    assert body["kind"] == "progressive"
    assert body["url"] == "https://x/prog.mp4"


def test_streams_cap_applies_across_both_paths(client):
    # A 720p cap must drop the 1080p video-only format, leaving the muxed 720p.
    with _patch_ydl(_info([PROG, VONLY, AONLY])):
        resp = client.get("/api/videos/vid000000001/streams?max_height=720")
    body = resp.json()
    assert body["kind"] == "progressive"
    assert body["height"] == 720


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


def test_related_videos_tiktok_returns_empty(client):
    resp = client.get("/api/videos/user123:room1/related", params={"platform": "tiktok"})
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_related_videos_network_error(client):
    fake_client = FakeAsyncClient(error=httpx.RequestError("boom"))
    with patch.object(httpx, "AsyncClient", lambda **kw: fake_client):
        resp = client.get("/api/videos/vid000000001/related")
    assert resp.status_code == 502


# ─── YouTube ~60 s delivery cap (see ytdlp._delivery_truncated) ───

def _gv_url(itag: str, clen: int, tag: str) -> str:
    return (
        f"https://rr1---sn-x.googlevideo.com/videoplayback?expire=1&itag={itag}"
        f"&clen={clen}&mime=video%2Fmp4&sig={tag}"
    )


def _gv_info(tag: str):
    return _info(
        [
            {"format_id": "140", "url": _gv_url("140", 36_000_000, tag),
             "ext": "m4a", "acodec": "mp4a.40.2", "vcodec": "none", "abr": 129,
             "protocol": "https"},
            {"format_id": "137", "url": _gv_url("137", 830_000_000, tag),
             "ext": "mp4", "acodec": "none", "vcodec": "avc1.640028", "protocol": "https",
             "height": 1080, "width": 1920, "tbr": 2957},
        ]
    )


class SequenceYDL(FakeYDL):
    """FakeYDL handing out one info per extraction, tracking the call count."""

    def __init__(self, infos):
        self._infos = list(infos)
        self.calls = 0

    def __call__(self, opts):
        return self

    def extract_info(self, url, download=False):
        info = self._infos[min(self.calls, len(self._infos) - 1)]
        self.calls += 1
        return info


def _patch_range_probe(capped_tags):
    """Fake httpx.Client whose ranged GET 403s for URLs of a capped bucket."""
    probed: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            probed.append((url, (headers or {}).get("Range", "")))
            capped = any(f"sig={tag}" in url for tag in capped_tags)
            return httpx.Response(403 if capped else 206)

    return patch.object(ytdlp.httpx, "Client", FakeClient), probed


def test_streams_reresolves_when_youtube_caps_delivery(client):
    """A bucket that 403s past ~60 s is discarded: the next extraction is used."""
    ydl = SequenceYDL([_gv_info("capped"), _gv_info("healthy")])
    probe, probed = _patch_range_probe({"capped"})
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        resp = client.get("/api/videos/vid000000001/streams")

    assert resp.status_code == 200
    assert "sig=healthy" in resp.json()["video_url"]
    assert ydl.calls == 2
    # The probe asks for the last byte only, on the smallest (audio) format.
    assert probed[0][1] == "bytes=35999999-35999999"
    assert "itag=140" in probed[0][0]


def test_streams_keeps_first_resolution_when_delivery_is_whole(client):
    ydl = SequenceYDL([_gv_info("healthy"), _gv_info("other")])
    probe, probed = _patch_range_probe(set())
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        resp = client.get("/api/videos/vid000000001/streams")

    assert "sig=healthy" in resp.json()["video_url"]
    assert ydl.calls == 1
    assert len(probed) == 1


def test_streams_gives_up_after_three_capped_resolutions(client):
    """Never fail playback: a capped stream still beats no stream at all."""
    ydl = SequenceYDL([_gv_info("capped")])
    probe, _ = _patch_range_probe({"capped"})
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        resp = client.get("/api/videos/vid000000001/streams")

    assert resp.status_code == 200
    assert ydl.calls == ytdlp._TRUNCATION_ATTEMPTS


def test_streams_probe_failure_does_not_block_playback(client):
    ydl = SequenceYDL([_gv_info("healthy")])

    class BoomClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            raise httpx.ConnectError("probe down")

    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), patch.object(
        ytdlp.httpx, "Client", BoomClient
    ):
        resp = client.get("/api/videos/vid000000001/streams")

    assert resp.status_code == 200
    assert ydl.calls == 1


def test_non_googlevideo_streams_are_not_probed(client):
    ydl = SequenceYDL([_info([BC_PROG], url=BC_URL)])
    probe, probed = _patch_range_probe(set())
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        resp = client.get("/api/videos/vid000000001/streams")

    assert resp.status_code == 200
    assert probed == []


@pytest.fixture(autouse=True)
def _no_background_bucket_checks():
    """Keep the delayed bucket probe off real timers, and buckets test-local."""
    scheduled: list[tuple] = []
    ytdlp._capped_buckets.clear()
    ytdlp._bucket_checks_pending.clear()
    with patch.object(
        ytdlp, "_spawn_delayed",
        lambda delay, func, *args: scheduled.append((delay, func, args)),
    ):
        yield scheduled
    ytdlp._capped_buckets.clear()
    ytdlp._bucket_checks_pending.clear()


# ─── Same bucket, seen through a live's HLS playlist (no file to range-probe) ──

# Each fake bucket carries its own experiment ids, the way a real one does.
_BUCKET_IDS = {"capped": "51946838", "healthy": "51946837"}


def _fexp_for(tag: str) -> str:
    return _BUCKET_IDS.get(tag, f"519468{abs(hash(tag)) % 90 + 10}")


def _hls_playlist_url(itag: str, tag: str) -> str:
    return (
        "https://manifest.googlevideo.com/api/manifest/hls_playlist/expire/1"
        f"/itag/{itag}/source/yt_live_broadcast/fexp/{_fexp_for(tag)}"
        f"/sig/{tag}/playlist/index.m3u8"
    )


def _live_info(tag: str):
    """A YouTube live: HLS variants only, no `clen`, no duration."""
    return _info(
        is_live=True,
        formats=[
            {"format_id": "91", "url": _hls_playlist_url("91", tag), "ext": "mp4",
             "protocol": "m3u8_native", "acodec": "mp4a.40.5", "vcodec": "avc1.4D400C",
             "height": 144, "width": 256, "tbr": 269},
            {"format_id": "96", "url": _hls_playlist_url("96", tag), "ext": "mp4",
             "protocol": "m3u8_native", "acodec": "mp4a.40.2", "vcodec": "avc1.4D4028",
             "height": 1080, "width": 1920, "tbr": 4561},
        ],
    )


def test_live_stream_is_flagged_so_clients_skip_resume(client):
    """A YouTube live keeps a plain id: `is_live` is all that tells it apart."""
    ydl = SequenceYDL([_live_info("healthy")])
    probe, _ = _patch_hls_probe(set())
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        resp = client.get("/api/videos/vid000000001/streams")

    assert resp.json()["is_live"] is True


def test_vod_stream_is_not_flagged_live(client):
    ydl = SequenceYDL([_gv_info("healthy")])
    probe, _ = _patch_range_probe(set())
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        resp = client.get("/api/videos/vid000000001/streams")

    assert resp.json()["is_live"] is False


def _age_info_cache(seconds: float) -> None:
    """Backdate every cache entry, without touching the process-wide clock."""
    for key, (stamp, info) in list(ytdlp._INFO_CACHE.items()):
        ytdlp._INFO_CACHE[key] = (stamp - seconds, info)


def test_live_extraction_is_not_cached_past_its_url_lifetime(client):
    """Live URLs die ~1 min in; a retry must re-extract, not get them back."""
    ydl = SequenceYDL([_live_info("first"), _live_info("second")])
    probe, _ = _patch_hls_probe(set())
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        first = client.get("/api/videos/vid000000001/streams").json()["url"]
        # Aged past the live TTL, still well inside the VOD one.
        _age_info_cache(ytdlp._INFO_CACHE_TTL_LIVE + 1)
        second = client.get("/api/videos/vid000000001/streams").json()["url"]

    assert "first" in first
    assert "second" in second, "a live must not be served from a stale cache entry"
    assert ydl.calls == 2


def test_vod_extraction_is_still_cached(client):
    """The same ageing must not evict a VOD: its URLs last hours."""
    ydl = SequenceYDL([_gv_info("first"), _gv_info("second")])
    probe, _ = _patch_range_probe(set())
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        client.get("/api/videos/vid000000001/streams")
        _age_info_cache(ytdlp._INFO_CACHE_TTL_LIVE + 1)
        again = client.get("/api/videos/vid000000001/streams").json()["video_url"]

    assert "sig=first" in again
    assert ydl.calls == 1


def _patch_hls_probe(capped_tags):
    """Fake client serving media playlists whose segments 403 for a capped bucket."""
    probed: list[str] = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            probed.append(url)
            capped = any(f"/sig/{tag}/" in url or f"sig={tag}" in url for tag in capped_tags)
            if "/playlist/index.m3u8" in url:  # the media playlist itself is served
                body = f"#EXTM3U\n#EXTINF:5.0,\nhttps://rr1---sn-x.googlevideo.com/seg?sig={_tag_of(url)}\n"
                return httpx.Response(200, text=body)
            return httpx.Response(403 if capped else 206)

    return patch.object(ytdlp.httpx, "Client", FakeClient), probed


def _tag_of(url: str) -> str:
    return url.split("/sig/", 1)[1].split("/", 1)[0]


def test_live_reresolves_when_hls_segments_are_forbidden(client):
    """A live whose every segment 403s freezes the picture: re-roll the bucket."""
    ydl = SequenceYDL([_live_info("capped"), _live_info("healthy")])
    probe, probed = _patch_hls_probe({"capped"})
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        resp = client.get("/api/videos/vid000000001/streams")

    assert resp.status_code == 200
    assert "healthy" in resp.json()["url"]
    assert ydl.calls == 2
    # Probed through the cheapest variant, then its first segment.
    assert "/itag/91/" in probed[0]
    assert probed[1].startswith("https://rr1---sn-x.googlevideo.com/seg")


def test_live_keeps_first_resolution_when_segments_are_served(client):
    ydl = SequenceYDL([_live_info("healthy"), _live_info("other")])
    probe, probed = _patch_hls_probe(set())
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        resp = client.get("/api/videos/vid000000001/streams")

    assert resp.status_code == 200
    assert "healthy" in resp.json()["url"]
    assert ydl.calls == 1
    assert len(probed) == 2  # playlist + one segment, nothing more


def test_live_still_plays_when_every_bucket_is_capped(client):
    """Never fail playback, even when all attempts come back poisoned."""
    ydl = SequenceYDL([_live_info("capped")])
    probe, _ = _patch_hls_probe({"capped"})
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        resp = client.get("/api/videos/vid000000001/streams")

    assert resp.status_code == 200
    assert ydl.calls == ytdlp._TRUNCATION_ATTEMPTS


# ─── Self-calibrating bucket quarantine (nothing about fexp is hardcoded) ─────

def _fexp_playlist(ids: str) -> str:
    return (
        "https://manifest.googlevideo.com/api/manifest/hls_playlist/expire/1"
        f"/itag/91/fexp/{ids}/sig/x/playlist/index.m3u8"
    )


def test_fexp_signature_is_read_from_path_and_query():
    assert ytdlp._fexp_signature(_fexp_playlist("1,2,3")) == "1,2,3"
    assert ytdlp._fexp_signature("https://x/videoplayback?a=1&fexp=9,8") == "9,8"
    assert ytdlp._fexp_signature("https://x/none") is None


def test_served_live_schedules_a_delayed_bucket_check(
    client, _no_background_bucket_checks
):
    """The verdict cannot be taken now, so it is booked for a minute later."""
    ydl = SequenceYDL([_live_info("healthy")])
    probe, _ = _patch_hls_probe(set())
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        client.get("/api/videos/vid000000001/streams")

    assert len(_no_background_bucket_checks) == 1
    delay, func, _ = _no_background_bucket_checks[0]
    assert delay == ytdlp._BUCKET_VERIFY_DELAY
    assert func is ytdlp._verify_bucket


def test_vod_schedules_no_bucket_check(client, _no_background_bucket_checks):
    ydl = SequenceYDL([_gv_info("healthy")])
    probe, _ = _patch_range_probe(set())
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        client.get("/api/videos/vid000000001/streams")

    assert _no_background_bucket_checks == []


def test_bucket_check_quarantines_a_signature_that_went_dead():
    probe, _ = _patch_hls_probe({"capped"})
    with probe:
        ytdlp._verify_bucket("51946838", _fexp_playlist("51946838").replace(
            "/sig/x/", "/sig/capped/"))

    assert ytdlp._bucket_quarantined("51946838")
    assert not ytdlp._bucket_quarantined("51946837")


def test_bucket_check_leaves_a_healthy_signature_alone():
    probe, _ = _patch_hls_probe(set())
    with probe:
        ytdlp._verify_bucket("51946837", _fexp_playlist("51946837"))

    assert not ytdlp._bucket_quarantined("51946837")


def test_quarantine_expires():
    ytdlp._quarantine_bucket("stale")
    assert ytdlp._bucket_quarantined("stale")
    ytdlp._capped_buckets["stale"] -= ytdlp._BUCKET_QUARANTINE_TTL + 1
    assert not ytdlp._bucket_quarantined("stale")


def test_quarantined_bucket_is_rolled_again(client):
    """An extraction landing in a known-dead bucket is thrown away, not served."""
    ydl = SequenceYDL([_live_info("capped"), _live_info("healthy")])
    probe, _ = _patch_hls_probe(set())  # both look fine right now — that is the point
    import yt_dlp

    # What a previous playback taught us: the bucket behind "capped" goes dead.
    ytdlp._quarantine_bucket(_fexp_for("capped"))

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        resp = client.get("/api/videos/vid000000001/streams")

    assert "healthy" in resp.json()["url"]
    assert ydl.calls == 2


# ─── forget_dead_stream : éviction du cache sur 403 observé en lecture ───


def _cache_entry(watch_url: str, media_url: str) -> None:
    import time

    ytdlp._INFO_CACHE[watch_url] = (time.monotonic(), {"formats": [{"url": media_url}]})


def test_forget_dead_stream_evicts_only_the_matching_extraction():
    _cache_entry(
        "https://www.youtube.com/watch?v=dead",
        "https://rr1---sn-x.googlevideo.com/videoplayback?expire=1&id=o-dead&itag=137",
    )
    _cache_entry(
        "https://www.youtube.com/watch?v=alive",
        "https://rr1---sn-x.googlevideo.com/videoplayback?expire=1&id=o-alive&itag=137",
    )

    # Un autre itag/host de la même extraction partage le même id.
    assert ytdlp.forget_dead_stream(
        "https://rr2---sn-y.googlevideo.com/videoplayback?expire=1&id=o-dead&itag=140"
    )

    assert "https://www.youtube.com/watch?v=dead" not in ytdlp._INFO_CACHE
    assert "https://www.youtube.com/watch?v=alive" in ytdlp._INFO_CACHE


def test_forget_dead_stream_matches_live_path_style_ids():
    _cache_entry(
        "https://www.youtube.com/watch?v=live1",
        "https://manifest.googlevideo.com/api/manifest/hls_playlist/expire/1"
        "/id/live1.2~xyz/itag/96/playlist/index.m3u8",
    )

    # Les segments d'un live portent le même id, en style chemin.
    assert ytdlp.forget_dead_stream(
        "https://rr3---sn-z.googlevideo.com/videoplayback/id/live1.2~xyz/itag/96/sq/42"
    )

    assert not ytdlp._INFO_CACHE


def test_forget_dead_stream_ignores_foreign_hosts():
    _cache_entry(
        "https://www.youtube.com/watch?v=x",
        "https://rr1---sn-x.googlevideo.com/videoplayback?id=o-x&itag=137",
    )

    assert not ytdlp.forget_dead_stream("https://cdn.example/videoplayback?id=o-x")
    assert ytdlp._INFO_CACHE
