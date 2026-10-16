"""Search, listings, details and stream resolution with yt-dlp mocked."""

from __future__ import annotations

import json
from pathlib import Path
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


class _SequenceYDL:
    """Fake YoutubeDL serving one info per construction.

    Records the opts of every construction and, for a session extraction, the
    content of the cookie file yt-dlp was given AT EXTRACTION TIME (the copy is
    deleted afterwards). ``on_close`` mimics yt-dlp rewriting that file when
    the YoutubeDL closes (rotated cookies).
    """

    def __init__(self, infos, on_close=None):
        self._infos = list(infos)
        self.opts_list = []
        self.cookie_texts = []
        self._on_close = on_close

    def __call__(self, opts):
        self.opts_list.append(opts)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        cookiefile = self.opts_list[-1].get("cookiefile")
        if cookiefile and self._on_close:
            self._on_close(Path(cookiefile))
        return False

    def extract_info(self, url, download=False):
        cookiefile = self.opts_list[-1].get("cookiefile")
        self.cookie_texts.append(Path(cookiefile).read_text() if cookiefile else None)
        info = self._infos.pop(0) if self._infos else {}
        if isinstance(info, Exception):
            raise info
        return info


_COOKIES_TEXT = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tLOGIN_INFO\tfake\n"
    ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tSAPISID\tfake\n"
)


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


class _RecordingYDL(FakeYDL):
    def extract_info(self, url, download=False):
        self.url = url
        return super().extract_info(url, download)


def test_channel_videos_by_views_reads_popular_playlist(client):
    """sort=views lists the channel's auto-generated "Popular videos"
    playlist (UULP + id without UC), paged like the Videos tab."""
    entries = [
        {"id": "vid000000001", "title": "Hit", "view_count": 9000},
        {"id": "vid000000002", "title": "Less", "view_count": 10},
    ]
    ydl = _RecordingYDL({"entries": entries, "title": "Popular videos"})
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl):
        resp = client.get(
            "/api/channels/UCtest000000000000000000/videos",
            params={"sort": "views", "limit": 3, "offset": 10},
        )
    assert resp.status_code == 200
    assert ydl.url == "https://www.youtube.com/playlist?list=UULPtest000000000000000000"
    assert ydl._opts["playlist_items"] == "11:14"
    assert [i["video_id"] for i in resp.json()["items"]] == ["vid000000001", "vid000000002"]


def test_channel_videos_by_views_without_popular_playlist(client):
    """Channels without long-form uploads have no popular playlist: empty."""
    error = RuntimeError("ERROR: [youtube:tab] UULPx: YouTube said: The playlist does not exist.")
    with _patch_ydl(error):
        resp = client.get(
            "/api/channels/UCtest000000000000000000/videos", params={"sort": "views"}
        )
    assert resp.status_code == 200
    assert resp.json()["items"] == []
    assert resp.json()["has_more"] is False


def test_channel_videos_by_views_non_uc_id_ranks_locally(client):
    entries = [
        {"id": "vid000000001", "title": "Low", "view_count": 5},
        {"id": "vid000000002", "title": "High", "view_count": 500},
        {"id": "vid000000003", "title": "Unknown"},
    ]
    ydl = _RecordingYDL({"entries": entries, "title": "Chan"})
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl):
        resp = client.get("/api/channels/legacyid/videos", params={"sort": "views"})
    assert resp.status_code == 200
    assert ydl.url == "https://www.youtube.com/channel/legacyid/videos"
    assert [i["video_id"] for i in resp.json()["items"]] == [
        "vid000000002", "vid000000001", "vid000000003"
    ]


def test_channel_videos_by_views_unsupported_platform(client):
    resp = client.get(
        "/api/channels/chan0000/videos", params={"platform": "twitch", "sort": "views"}
    )
    assert resp.status_code == 501
    # Unknown sort keys are rejected by validation.
    assert client.get(
        "/api/channels/UCtest000000000000000000/videos", params={"sort": "oldest"}
    ).status_code == 422


def test_channel_videos_search_ignores_views_sort(client):
    """A search keeps its own order: sort=views does not reroute it."""
    entries = [
        {"id": "aaaaaaaaaaaa", "title": "Cooking pasta", "view_count": 1},
        {"id": "bbbbbbbbbbbb", "title": "PASTA machine review", "view_count": 99},
    ]
    with _patch_ydl({"entries": entries, "title": "Chan"}):
        resp = client.get(
            "/api/channels/chan0000/videos",
            params={"platform": "twitch", "q": "pasta", "sort": "views"},
        )
    assert resp.status_code == 200
    assert [i["video_id"] for i in resp.json()["items"]] == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"]


def test_channel_videos_query_filters_platforms_without_search(client):
    """Twitch has no in-channel search: the listing is filtered on title."""
    entries = [
        {"id": "aaaaaaaaaaaa", "title": "Cooking pasta"},
        {"id": "bbbbbbbbbbbb", "title": "PASTA machine review"},
        {"id": "cccccccccccc", "title": "Bread"},
    ]
    with _patch_ydl({"entries": entries, "title": "Chan"}):
        resp = client.get(
            "/api/channels/chan0000/videos",
            params={"platform": "twitch", "q": "pasta"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert [i["video_id"] for i in body["items"]] == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"]
    assert body["has_more"] is False


def test_channel_videos_rejects_empty_query(client):
    resp = client.get("/api/channels/UCtest000000000000000000/videos", params={"q": ""})
    assert resp.status_code == 422


class RecordingYDL(FakeYDL):
    def extract_info(self, url, download=False):
        self.url = url
        return super().extract_info(url, download)


def test_channel_playlists_youtube_tab(client):
    """The Playlists tab is listed flat; lockups' bogus "Playlist" channel is
    replaced by the tab's own channel."""
    entries = [
        {"id": f"PLtest{i}", "title": f"List {i}", "_type": "url", "ie_key": "YoutubeTab",
         "channel": "Playlist", "uploader": "Playlist",
         "url": f"https://www.youtube.com/playlist?list=PLtest{i}",
         "thumbnails": [{"url": f"https://i.ytimg.com/vi/x{i}/hqdefault.jpg"}]}
        for i in range(3)
    ]
    ydl = RecordingYDL({
        "entries": entries, "title": "Chan - Playlists", "channel": "Chan",
        "channel_id": "UCtest000000000000000000",
    })
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl):
        resp = client.get(
            "/api/channels/UCtest000000000000000000/playlists",
            params={"limit": 2, "offset": 4},
        )
    assert resp.status_code == 200
    assert ydl.url == "https://www.youtube.com/channel/UCtest000000000000000000/playlists"
    assert ydl._opts["playlist_items"] == "5:7"
    body = resp.json()
    assert [i["video_id"] for i in body["items"]] == ["PLtest0", "PLtest1"]
    assert all(i["kind"] == "playlist" for i in body["items"])
    assert body["items"][0]["channel_title"] == "Chan"
    assert body["items"][0]["channel_id"] == "UCtest000000000000000000"
    assert body["items"][0]["thumbnail_url"] == "https://i.ytimg.com/vi/x0/hqdefault.jpg"
    assert body["has_more"] is True
    assert body["channel"]["title"] == "Chan"


def test_channel_playlists_without_tab_is_empty(client):
    err = RuntimeError("ERROR: [youtube:tab] UCx: This channel does not have a playlists tab")
    with _patch_ydl(err):
        resp = client.get("/api/channels/UCtest000000000000000000/playlists")
    assert resp.status_code == 200
    assert resp.json()["items"] == []
    assert resp.json()["has_more"] is False


def test_channel_playlists_upstream_error(client):
    with _patch_ydl(RuntimeError("boom")):
        resp = client.get("/api/channels/UCtest000000000000000000/playlists")
    assert resp.status_code == 502


@pytest.mark.parametrize("platform", ["odysee", "twitch", "tiktok", "crowdbunker"])
def test_channel_playlists_unsupported_platforms(client, platform):
    resp = client.get("/api/channels/chan/playlists", params={"platform": platform})
    assert resp.status_code == 501


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


# ─── stream resolution and the pushed session ───
#
# yt-dlp only reads cookies from `cookiefile` (an unknown `cookies` option is
# silently ignored — the 2026-10-15 version of this code was a no-op because
# of it) and rewrites that file when it closes. So every session extraction
# gets a private copy of the jar, rotated cookies are merged back through the
# guarded _save_cookies, and any session failure falls back to anonymous.


@pytest.fixture()
def session_file(tmp_path, monkeypatch):
    cookie_file = tmp_path / "youtube_cookies.txt"
    cookie_file.write_text(_COOKIES_TEXT, encoding="utf-8")
    monkeypatch.setattr(ytdlp, "_cookie_file", cookie_file)
    return cookie_file


def _streams(client, fake):
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", fake):
        return client.get("/api/videos/vid000000001/streams")


def test_streams_hand_ytdlp_a_private_copy_of_the_session(client, session_file):
    fake = _SequenceYDL([_info([PROG])])
    resp = _streams(client, fake)
    assert resp.status_code == 200
    assert len(fake.opts_list) == 1
    opts = fake.opts_list[0]
    assert "cookies" not in opts  # the option yt-dlp ignores
    copy = Path(opts["cookiefile"])
    assert copy != session_file  # yt-dlp rewrites it in place: never the live jar
    assert "LOGIN_INFO" in fake.cookie_texts[0]
    assert not copy.exists(), "the private copy is removed after the extraction"


def test_streams_stay_anonymous_without_cookie_file(client, monkeypatch):
    monkeypatch.setattr(ytdlp, "_cookie_file", None)
    fake = _SequenceYDL([_info([PROG])])
    assert _streams(client, fake).status_code == 200
    assert len(fake.opts_list) == 1
    assert "cookiefile" not in fake.opts_list[0]


def test_streams_stay_anonymous_when_cookie_file_is_unreadable(client, tmp_path, monkeypatch):
    cookie_file = tmp_path / "youtube_cookies.txt"
    cookie_file.write_text("not a netscape file\n", encoding="utf-8")
    monkeypatch.setattr(ytdlp, "_cookie_file", cookie_file)
    fake = _SequenceYDL([_info([PROG])])
    assert _streams(client, fake).status_code == 200
    assert "cookiefile" not in fake.opts_list[0]


def test_streams_stay_anonymous_when_session_lacks_login_info(client, tmp_path, monkeypatch):
    cookie_file = tmp_path / "youtube_cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tSAPISID\tfake\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ytdlp, "_cookie_file", cookie_file)
    fake = _SequenceYDL([_info([PROG])])
    assert _streams(client, fake).status_code == 200
    assert "cookiefile" not in fake.opts_list[0]


def test_streams_fall_back_to_anonymous_when_the_session_fails(client, session_file):
    from yt_dlp.utils import DownloadError

    fake = _SequenceYDL([DownloadError("Sign in to confirm you're not a bot"), _info([PROG])])
    assert _streams(client, fake).status_code == 200
    assert len(fake.opts_list) == 2
    assert "cookiefile" in fake.opts_list[0]
    assert "cookiefile" not in fake.opts_list[1]
    assert not Path(fake.opts_list[0]["cookiefile"]).exists()


def test_streams_persist_rotated_session_cookies(client, session_file):
    def rotate(copy):
        with copy.open("a") as fh:
            fh.write(".youtube.com\tTRUE\t/\tTRUE\t9999999999\tSIDCC\trotated\n")

    fake = _SequenceYDL([_info([PROG])], on_close=rotate)
    assert _streams(client, fake).status_code == 200
    text = session_file.read_text()
    assert "rotated" in text and "LOGIN_INFO" in text
    assert (session_file.stat().st_mode & 0o777) == 0o600


def test_streams_never_persist_a_rotation_that_drops_the_session(client, session_file):
    """yt-dlp rewriting a jar without LOGIN_INFO must not reach the live file."""
    before = session_file.read_text()

    def drop_login(copy):
        copy.write_text(
            "# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tSAPISID\tother\n"
        )

    fake = _SequenceYDL([_info([PROG])], on_close=drop_login)
    assert _streams(client, fake).status_code == 200
    assert session_file.read_text() == before


def test_real_ytdlp_reads_the_staged_session(session_file):
    """Against the real YoutubeDL (no network): the jar it loads is the session.

    Guards the 2026-10-15 regression where the jar went into an option yt-dlp
    never reads, and every fake-based test above still passed.
    """
    import yt_dlp

    copy = ytdlp._session_cookie_copy()
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "cookiefile": str(copy)}) as ydl:
            assert "LOGIN_INFO" in {c.name for c in ydl.cookiejar}
        with yt_dlp.YoutubeDL({"quiet": True, "cookies": object()}) as ydl:
            assert not {c.name for c in ydl.cookiejar}  # `cookies` is ignored
    finally:
        copy.unlink(missing_ok=True)


# A split pair refusing its first byte (anonymous TV client, measured
# 2026-10-16) is a dead client, not a capped bucket: switch path, don't re-roll.

_DEAD = {"title": "A video", "duration": 212, "dead": True}


def _dead_marker(monkeypatch):
    monkeypatch.setattr(ytdlp, "_adaptive_head_dead", lambda info: bool(info.get("dead")))


def test_dead_session_split_switches_to_anonymous(client, session_file, monkeypatch):
    _dead_marker(monkeypatch)
    fake = _SequenceYDL([dict(_DEAD, formats=[PROG]), _info([VONLY, AONLY])])
    resp = _streams(client, fake)
    assert resp.status_code == 200
    assert resp.json()["kind"] == "split"
    assert ["cookiefile" in o for o in fake.opts_list] == [True, False]


def test_healthy_session_needs_no_anonymous_round(client, session_file, monkeypatch):
    _dead_marker(monkeypatch)
    fake = _SequenceYDL([_info([VONLY, AONLY])])
    assert _streams(client, fake).status_code == 200
    assert len(fake.opts_list) == 1  # healthy session: no anonymous round


HLS_VONLY = {"format_id": "616", "url": "https://x/v616.m3u8", "protocol": "m3u8_native",
             "vcodec": "avc1", "acodec": "none", "height": 1080,
             "manifest_url": "https://x/master.m3u8"}


def test_every_path_dead_serves_the_hls_master(client, session_file, monkeypatch):
    _dead_marker(monkeypatch)
    dead = dict(_DEAD, formats=[VONLY, AONLY, HLS_VONLY])
    fake = _SequenceYDL([dead, dict(dead)])
    resp = _streams(client, fake)
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "hls"
    assert body["url"] == "https://x/master.m3u8"
    assert len(fake.opts_list) == 2  # one per path, no truncation re-rolls
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", _SequenceYDL([])):
        mpd = client.get("/api/videos/vid000000001/mpd")
    assert mpd.status_code == 404  # cached dead extraction: no DASH


def test_dead_split_without_session_is_resolved_once(client, monkeypatch):
    monkeypatch.setattr(ytdlp, "_cookie_file", None)
    _dead_marker(monkeypatch)
    fake = _SequenceYDL([dict(_DEAD, formats=[VONLY, AONLY, HLS_VONLY])])
    resp = _streams(client, fake)
    assert resp.json()["kind"] == "hls"
    assert len(fake.opts_list) == 1


def test_adaptive_head_probe_checks_the_split_pair():
    info = _gv_info("dead")
    probe, probed = _patch_range_probe(set(), dead_tags={"dead"})
    with probe:
        assert ytdlp._adaptive_head_dead(info) is True
    assert probed[0][1] == "bytes=0-1" and "itag=137" in probed[0][0]
    probe, probed = _patch_range_probe({"capped"})
    with probe:
        assert ytdlp._adaptive_head_dead(_gv_info("capped")) is False
    assert {r for _, r in probed} == {"bytes=0-1"}


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


# Real shape of a multi-audio YouTube video (visionos client): dubs carry no
# "dub" marker anywhere, just "German (DE), low" with language_preference -1;
# the original track reads "… original (default)" with language_preference 10.
DUB = {**AONLY, "format_id": "140-1", "url": "https://x/audio-dub.m4a",
       "format_note": "German (DE), low", "language": "de-DE",
       "language_preference": -1, "abr": 192}
ORIG = {**AONLY, "format_id": "140-0", "url": "https://x/audio-orig.m4a",
        "format_note": "English (US) original (default), low",
        "language": "en-US", "language_preference": 10, "abr": 128}


def test_streams_split_audio_prefers_original_over_dub(client):
    # A bare bitrate max() used to hand out the higher-bitrate auto-dub.
    with _patch_ydl(_info([VONLY, DUB, ORIG])):
        resp = client.get("/api/videos/vid000000001/streams")
    body = resp.json()
    assert body["kind"] == "split"
    assert body["audio_url"] == "https://x/audio-orig.m4a"


def test_split_mp4_audio_prefers_original_over_dub():
    # The DASH/MPD path (browser >360p) had the same bare bitrate max() and
    # kept serving auto-dubbed audio after the /streams path was fixed.
    vonly = {**VONLY, "ext": "mp4", "width": 1920}
    dub = {**DUB, "ext": "m4a"}
    orig = {**ORIG, "ext": "m4a"}
    with _patch_ydl(_info([vonly, dub, orig])):
        split = ytdlp._extract_split_mp4(
            "https://www.youtube.com/watch?v=dubmpd000001", 1440
        )
    assert split is not None
    assert split.audio_url == "https://x/audio-orig.m4a"


def test_streams_split_audio_prefers_opus_over_m4a(client):
    # Same soundtrack in both codecs: Opus/WebM seeks reliably through the
    # byte proxy where the m4a rendition wedged the mobile demuxer.
    opus = {**AONLY, "format_id": "251", "url": "https://x/audio.webm",
            "acodec": "opus", "abr": 118}
    with _patch_ydl(_info([VONLY, AONLY, opus])):
        resp = client.get("/api/videos/vid000000001/streams")
    body = resp.json()
    assert body["kind"] == "split"
    assert body["audio_url"] == "https://x/audio.webm"


def test_streams_audio_only(client):
    with _patch_ydl(_info([PROG, AONLY])):
        resp = client.get(
            "/api/videos/vid000000001/streams", params={"audio_only": True}
        )
    body = resp.json()
    assert body["url"] == "https://x/audio.m4a"


def test_streams_audio_only_prefers_original_over_dub(client):
    # Third site of the same trap: the audio_only branch used a bare bitrate
    # max() and could hand out an auto-dubbed rendition.
    with _patch_ydl(_info([PROG, DUB, ORIG])):
        resp = client.get(
            "/api/videos/vid000000001/streams", params={"audio_only": True}
        )
    assert resp.json()["url"] == "https://x/audio-orig.m4a"


def test_streams_dub_note_penalized_without_language_preference(client):
    # Fallback for extractors that only mark the dub in format_note.
    dub = {**AONLY, "format_id": "140-1", "url": "https://x/audio-dub.m4a",
           "format_note": "English (United States) dubbed-auto", "abr": 192}
    orig = {**AONLY, "format_id": "140-0", "url": "https://x/audio-orig.m4a",
            "format_note": "French (France) original (default)", "abr": 128}
    with _patch_ydl(_info([VONLY, dub, orig])):
        resp = client.get("/api/videos/vid000000001/streams")
    body = resp.json()
    assert body["kind"] == "split"
    assert body["audio_url"] == "https://x/audio-orig.m4a"


def test_streams_audio_only_keeps_bitrate_order_across_codecs(client):
    # Unlike the split path, audio_only hands a raw URL to the client: no
    # Opus preference (a <audio src> consumer cannot play WebM/Opus), the
    # top-bitrate rendition wins as it always did.
    opus = {**AONLY, "format_id": "251", "url": "https://x/audio.webm",
            "acodec": "opus", "abr": 118}
    with _patch_ydl(_info([PROG, AONLY, opus])):
        resp = client.get(
            "/api/videos/vid000000001/streams", params={"audio_only": True}
        )
    assert resp.json()["url"] == "https://x/audio.m4a"


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
                  duration_text="4:20", thumb_url="https://i.ytimg.com/vi/rel0000001/hq.jpg",
                  views=None):
    rows = [{"metadataParts": [{"text": {"content": channel}}]}]
    if views is not None:
        # The InnerTube /next shape: ["144K", "4d ago"] second row.
        rows.append({"metadataParts": [
            {"text": {"content": views}},
            {"text": {"content": "4d ago"}},
        ]})
    return {
        "lockupViewModel": {
            "contentId": content_id,
            "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "metadata": {
                "lockupMetadataViewModel": {
                    "title": {"content": title},
                    "metadata": {
                        "contentMetadataViewModel": {
                            "metadataRows": rows
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
    def __init__(self, text, status_code=200, json_data=None):
        self.text = text
        self.status_code = status_code
        self._json_data = json_data

    def raise_for_status(self):
        return None

    def json(self):
        if self._json_data is None:
            raise ValueError("not json")
        return self._json_data


class FakeAsyncClient:
    def __init__(self, response=None, error=None, post_response=None, **kwargs):
        self._response = response
        self._error = error
        self._post_response = post_response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        if self._error is not None:
            raise self._error
        return self._response

    async def post(self, url, **kwargs):
        if self._error is not None:
            raise self._error
        return self._post_response or self._response


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


def test_related_videos_next_endpoint_carries_view_counts(client):
    """The InnerTube /next primary path parses the [\"144K\", ...] row."""
    next_data = {"contents": {"items": [_make_lockup(views="144K")]}}
    fake_client = FakeAsyncClient(post_response=FakeResponse("", json_data=next_data))
    with patch.object(httpx, "AsyncClient", lambda **kw: fake_client):
        resp = client.get("/api/videos/vid000000001/related")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["video_id"] == "rel0000001"
    assert items[0]["view_count"] == 144000


def test_parse_compact_count():
    assert ytdlp._parse_compact_count("144K") == 144000
    assert ytdlp._parse_compact_count("1.5B") == 1_500_000_000
    assert ytdlp._parse_compact_count("1,815,671") == 1815671
    assert ytdlp._parse_compact_count("37K") == 37000
    assert ytdlp._parse_compact_count("1234567") == 1234567
    # Ages and labels are not counts.
    assert ytdlp._parse_compact_count("4d ago") is None
    assert ytdlp._parse_compact_count("Streamed 1d ago") is None
    assert ytdlp._parse_compact_count("") is None


def test_streams_expose_view_count(client):
    """/streams rides on the same yt-dlp extraction: the count is free."""
    with _patch_ydl(_info([PROG, HLS], manifest_url="https://x/master.m3u8",
                          view_count=1234567)):
        resp = client.get("/api/videos/vid000000001/streams")
    assert resp.status_code == 200
    assert resp.json()["view_count"] == 1234567


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


def _patch_range_probe(capped_tags, dead_tags=frozenset()):
    """Fake httpx.Client modelling googlevideo's two failure modes.

    A capped bucket serves the file head (``bytes=0-1``) but 403s further in;
    a dead client (``dead_tags``) 403s from the very first byte.
    """
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
            rng = (headers or {}).get("Range", "")
            if any(f"sig={tag}" in url for tag in dead_tags):
                return httpx.Response(403)
            capped = any(f"sig={tag}" in url for tag in capped_tags)
            return httpx.Response(403 if capped and rng != "bytes=0-1" else 206)

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
    # One EOF truncation probe, then the 2-byte head probe of the split pair.
    assert [r for _, r in probed] == ["bytes=35999999-35999999", "bytes=0-1", "bytes=0-1"]


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


# ─── HLS vidéo seule (VOD visionos) et formats muxés morts (itag 18) ───


def test_streams_video_only_hls_never_served_alone(client):
    """Les variantes HLS des VOD YouTube (visionos) sont vidéo seule : les
    servir seules donne une vidéo muette — le chemin split doit gagner."""
    vhls = {"format_id": "270", "url": "https://x/1080-vonly.m3u8",
            "protocol": "m3u8_native", "vcodec": "avc1.640028", "acodec": "none",
            "height": 1080}
    ahls = {"format_id": "234", "url": "https://x/audio.m3u8",
            "protocol": "m3u8_native", "vcodec": "none", "acodec": None}
    with _patch_ydl(_info([vhls, ahls, VONLY, AONLY])):
        resp = client.get("/api/videos/vid000000001/streams")
    body = resp.json()
    assert body["kind"] == "split"
    assert body["video_url"] == "https://x/video.mp4"


def test_streams_hls_with_unknown_audio_codec_still_served(client):
    """Twitch et consorts : acodec None (inconnu) n'est pas « pas d'audio »."""
    unk = dict(HLS, acodec=None)
    with _patch_ydl(_info([unk], manifest_url="https://x/master.m3u8")):
        resp = client.get("/api/videos/vid000000001/streams")
    assert resp.json()["kind"] == "hls"


def _mux_url(tag: str) -> str:
    # Comme le vrai itag 18 : pas de clen → hors de portée de la sonde EOF.
    return f"https://rr1---sn-x.googlevideo.com/videoplayback?expire=1&itag=18&sig={tag}"


def _gv_info_with_muxed(tag: str, mux_tag: str):
    info = _gv_info(tag)
    info["formats"].append(
        {"format_id": "18", "url": _mux_url(mux_tag), "ext": "mp4",
         "acodec": "mp4a.40.2", "vcodec": "avc1.42001E", "protocol": "https",
         "height": 360, "width": 640}
    )
    return info


def test_dead_muxed_format_is_dropped(client):
    """itag 18 (android_vr) mort dès l'octet 0 : purgé, le 360p sert le split
    au lieu d'une URL morte — re-résoudre n'y changerait rien."""
    ydl = SequenceYDL([_gv_info_with_muxed("healthy", "deadmux")])
    probe, _ = _patch_range_probe(set(), dead_tags={"deadmux"})
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        resp = client.get("/api/videos/vid000000001/streams?max_height=360")

    body = resp.json()
    assert body["kind"] == "split"
    assert "deadmux" not in body["video_url"]
    assert ydl.calls == 1, "un client mort ne doit pas déclencher de re-roll"


def test_healthy_muxed_format_still_wins_its_tier(client):
    ydl = SequenceYDL([_gv_info_with_muxed("healthy", "goodmux")])
    probe, _ = _patch_range_probe(set())
    import yt_dlp

    with patch.object(yt_dlp, "YoutubeDL", ydl), probe:
        resp = client.get("/api/videos/vid000000001/streams?max_height=360")

    body = resp.json()
    assert body["kind"] == "progressive"
    assert "goodmux" in body["url"]


def test_split_prefers_direct_file_over_m3u8_at_equal_height(client):
    """visionos expose des variantes m3u8 vidéo seule au tbr plus élevé que le
    fichier https de même hauteur : le split doit servir le fichier direct."""
    vhls = {"format_id": "270", "url": "https://x/1080-vonly.m3u8",
            "protocol": "m3u8_native", "vcodec": "avc1.640028", "acodec": "none",
            "height": 1080, "tbr": 4911}
    with _patch_ydl(_info([vhls, VONLY, AONLY])):
        resp = client.get("/api/videos/vid000000001/streams")
    body = resp.json()
    assert body["kind"] == "split"
    assert body["video_url"] == "https://x/video.mp4"


# ─── concurrent callers share one extraction ───


def test_concurrent_callers_share_one_extraction():
    """/streams and /mpd land together: one yt-dlp run must serve both."""
    import threading
    import time as _time

    import yt_dlp

    calls = []
    release = threading.Event()

    class SlowYDL(FakeYDL):
        def extract_info(self, url, download=False):
            calls.append(url)
            release.wait(2)
            return {"title": "t", "formats": []}

    results = []
    with patch.object(yt_dlp, "YoutubeDL", SlowYDL(None)):
        threads = [
            threading.Thread(
                target=lambda: results.append(
                    ytdlp._extract_info_cached("https://www.youtube.com/watch?v=shared")
                )
            )
            for _ in range(3)
        ]
        for t in threads:
            t.start()
        _time.sleep(0.1)
        release.set()
        for t in threads:
            t.join(5)
    assert len(calls) == 1
    assert len(results) == 3 and all(r is results[0] for r in results)
    assert not ytdlp._INFLIGHT


def test_concurrent_callers_share_the_failure():
    import threading
    import time as _time

    import yt_dlp

    calls = []
    release = threading.Event()

    class FailingYDL(FakeYDL):
        def extract_info(self, url, download=False):
            calls.append(url)
            release.wait(2)
            raise RuntimeError("video unavailable")

    errors = []

    def run():
        try:
            ytdlp._extract_info_cached("https://www.youtube.com/watch?v=gone")
        except RuntimeError as exc:
            errors.append(str(exc))

    with patch.object(yt_dlp, "YoutubeDL", FailingYDL(None)):
        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        _time.sleep(0.1)
        release.set()
        for t in threads:
            t.join(5)
    assert len(calls) == 1
    assert errors == ["video unavailable"] * 2
    assert not ytdlp._INFLIGHT


def test_youtube_details_share_the_stream_extraction(client):
    """Watch page: /streams + /videos/{id} = one yt-dlp run, not two."""
    import yt_dlp

    calls = []

    class CountingYDL(FakeYDL):
        def extract_info(self, url, download=False):
            calls.append(url)
            return super().extract_info(url, download)

    info = _info(
        [{"url": "https://cdn.example/v.mp4", "vcodec": "avc1", "acodec": "mp4a", "height": 360}],
        id="vid000000001",
        description="Chapitres : 1:02 intro",
        channel_id="UCtest000000000000000000",
        channel="Chan",
        upload_date="20260920",
    )
    with patch.object(yt_dlp, "YoutubeDL", CountingYDL(info)):
        assert client.get("/api/videos/vid000000001/streams").status_code == 200
        details = client.get("/api/videos/vid000000001").json()
    assert len(calls) == 1
    assert details["description"] == "Chapitres : 1:02 intro"
    assert details["upload_date"] == "2026-09-20"
    assert details["thumbnail_url"] == "https://i.ytimg.com/vi/vid000000001/mqdefault.jpg"


def test_mpd_without_sidx_evicts_the_cached_extraction(client, monkeypatch):
    """A split pair whose head is refused must not be replayed from the cache."""
    from ytui_server.services import dash

    split = ytdlp.SplitMp4(
        video_url="https://rr1.googlevideo.com/videoplayback?id=dead", width=1920,
        height=1080, video_codec="avc1", video_bitrate=1, audio_url="https://x/a",
        audio_codec="mp4a", audio_bitrate=1, duration=10,
    )

    async def fake_split(url, max_height):
        return split

    async def no_ranges(client, url):
        return None

    forgotten = []
    monkeypatch.setattr(ytdlp, "resolve_split_mp4", fake_split)
    monkeypatch.setattr(dash, "probe_ranges", no_ranges)
    monkeypatch.setattr(ytdlp, "forget_dead_stream", lambda url: forgotten.append(url))
    resp = client.get("/api/videos/vid000000001/mpd")
    assert resp.status_code == 404
    assert forgotten == [split.video_url]


def _hls_lang(height, lang, pref):
    return {"format_id": f"{height}-{lang}", "url": f"https://x/{height}-{lang}.m3u8",
            "protocol": "m3u8_native", "vcodec": "avc1", "acodec": "mp4a.40.2",
            "height": height, "language": lang, "language_preference": pref}


def test_signed_in_hls_serves_the_original_language_variant(client):
    """Signed-in extraction (2026-10): muxed HLS variants per audio language in one
    master. The master would let the player pick a dub: serve the original's variant."""
    formats = [_hls_lang(1080, "en-US", -1), _hls_lang(1080, "fr-FR", 10),
               _hls_lang(720, "en-US", -1), _hls_lang(720, "fr-FR", 10)]
    with _patch_ydl(_info(formats, manifest_url="https://x/master.m3u8")):
        body = client.get("/api/videos/vid000000001/streams").json()
    assert body["kind"] == "hls"
    assert body["url"] == "https://x/1080-fr-FR.m3u8"
    assert body["height"] == 1080
