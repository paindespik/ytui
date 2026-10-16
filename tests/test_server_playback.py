"""YouTube VODs are resolved by the server, not by mpv's local yt-dlp.

Measured 2026-10-16: the desktop's yt-dlp picked an audio URL answering 403
(dlkIa_0hI4E) and mpv closed at once, while the server (account session,
dead-URL fallbacks, original-language pick) served it fine.
"""

from __future__ import annotations

import pytest

from ytui.api_client import YtuiApiError
from ytui.app import QUEUE_LOOKAHEAD, YtuiApp, _media_from_streams
from ytui.config import Config
from ytui.models import Video
from ytui.player.mpv import Media, PlayerStatus

YT = Video(video_id="dlkIa_0hI4E", title="Une vidéo")
SPLIT = {
    "kind": "split",
    "url": "https://gv/video",
    "video_url": "https://gv/video",
    "audio_url": "https://gv/audio",
    "title": "Titre serveur",
    "subtitles": [
        {"lang": "fr", "label": "fr (auto)", "url": "https://x/fr.vtt"},
        {"lang": "en", "label": "en", "url": "https://x/en.vtt"},
    ],
}


class FakeClient:
    def __init__(self, answer=SPLIT):
        self.answer = answer
        self.calls: list[dict] = []

    async def video_streams(self, video_id, platform="youtube", max_height=1440,
                            audio_only=False, sub_langs=None):
        self.calls.append(dict(video_id=video_id, platform=platform, max_height=max_height,
                               audio_only=audio_only, sub_langs=sub_langs))
        if isinstance(self.answer, Exception):
            raise self.answer
        return {**self.answer, "video_url": f"https://gv/{video_id}", "url": f"https://gv/{video_id}"}


class FakePlayer:
    """Records what the app hands to mpv; `queued` drives the lazy queue."""

    def __init__(self):
        self.played: list[tuple] = []
        self.enqueued: list = []
        self.queued = 0
        self.alive = True
        self.subs_added: list[str] = []
        self.snapshot = None
        self.entry_id = 1

    def process_alive(self):
        return self.alive

    async def status(self):
        if not self.alive:
            return None
        return PlayerStatus(paused=False, queued=self.queued)

    async def play(self, item, player, start=None):
        self.played.append((item, start))

    async def enqueue(self, item, player):
        self.enqueued.append(item)
        self.queued += 1
        return True

    async def playback_snapshot(self):
        return self.snapshot

    async def current_entry_id(self):
        return self.entry_id

    async def add_subtitles(self, media):
        self.subs_added.append(media.url)
        return len(media.subtitles)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    config = Config()
    config.server.url = "https://fake"
    config.server.token = "t"
    app = YtuiApp(config)
    app.client = FakeClient()
    app.player = FakePlayer()
    app.notify = lambda *a, **k: None
    app.run_worker = lambda *a, **k: None  # workers are driven by hand below
    app._record_watch = lambda video: None
    return app


async def test_youtube_vod_resolves_through_the_server(app):
    item = await app._playback_url(YT)
    assert isinstance(item, Media)
    assert item.url == "https://gv/dlkIa_0hI4E" and item.audio_url == "https://gv/audio"
    assert item.title == "Titre serveur" and item.video_id == YT.video_id
    assert [s.lang for s in item.subtitles] == ["fr", "en"]
    assert app.client.calls[0]["sub_langs"] == app.config.player.subtitle_langs


async def test_server_failure_falls_back_to_local_ytdlp(app):
    app.client = FakeClient(YtuiApiError(502, "Stream resolution failed"))
    assert await app._playback_url(YT) == YT.url


async def test_lives_keep_mpvs_own_ytdlp(app):
    app.active_lives = {YT.video_id: YT}
    assert await app._playback_url(YT) == YT.url
    assert app.client.calls == []
    app.active_lives = {}
    app.client = FakeClient({**SPLIT, "is_live": True})
    assert await app._playback_url(YT) == YT.url


async def test_other_platforms_are_untouched(app):
    odysee = Video(video_id="name:abc", title="x", platform="odysee")
    assert await app._playback_url(odysee) == odysee.url
    assert app.client.calls == []


async def test_audio_only_asks_the_server_for_audio(app):
    app.client = FakeClient({"kind": "progressive", "url": "https://gv/a", "title": "t"})
    item = await app._playback_url(YT, audio_only=True)
    assert isinstance(item, Media) and item.audio_url is None
    assert app.client.calls[0]["audio_only"] is True


def test_media_from_streams_kinds():
    assert _media_from_streams(YT, {"kind": "hls", "url": "https://m/x.m3u8"}).url == "https://m/x.m3u8"
    assert _media_from_streams(YT, {"kind": "split", "video_url": "v"}) is None  # no audio
    assert _media_from_streams(YT, {"kind": "weird", "url": "u"}) is None


async def test_play_all_resolves_lazily(app):
    videos = [Video(video_id=f"vid{i:08d}", title=f"V{i}") for i in range(10)]
    await app._play_all_async(videos)
    # Only the first is resolved up front: 9 extractions must not run first.
    assert len(app.client.calls) == 1
    assert len(app._pending) == 9
    await app._top_up_queue()
    assert len(app.player.enqueued) == QUEUE_LOOKAHEAD
    assert [m.video_id for m in app.player.enqueued] == ["vid00000001", "vid00000002"]
    # mpv advanced past one: the poller tops up again, in order.
    app.player.queued -= 1
    await app._top_up_queue()
    assert [m.video_id for m in app.player.enqueued][-1] == "vid00000003"


async def test_enqueue_keeps_order_behind_pending(app):
    app._pending.extend([Video(video_id="pending0001", title="p")])
    await app._enqueue_async(YT)
    assert [v.video_id for v in app._pending] == ["pending0001", YT.video_id]
    assert app.player.enqueued == []


async def test_poller_maps_resolved_urls_back_to_the_video(app):
    saved = []

    async def save(vid, position, duration, platform):
        saved.append((vid, platform))

    app.run_worker = lambda coro, **k: coro.close()
    media = await app._playback_url(YT)
    app.player.snapshot = (media.url, "Titre serveur", 30.0, 900.0)
    app._last_position_save = 0
    recorded = []
    app._record_watch = recorded.append
    await app._poll_player()
    # Identity comes from the resolved URL, not from mpv's googlevideo path.
    assert recorded and recorded[0].video_id == YT.video_id
    # Subtitles: added once per mpv playlist entry, again for a new entry.
    assert app.player.subs_added == [media.url]
    await app._poll_player()
    assert app.player.subs_added == [media.url]
    app.player.entry_id = 2
    await app._poll_player()
    assert app.player.subs_added == [media.url, media.url]


async def test_closed_player_drops_the_pending_queue(app):
    app._pending.extend([YT])
    app.player.alive = False
    await app._poll_player()
    assert not app._pending


async def test_quality_change_re_resolves_at_the_new_height(app, monkeypatch):
    monkeypatch.setattr("ytui.app.set_option", lambda *a: None)
    app._playing = YT
    app.player.snapshot = ("https://gv/old", "t", 120.0, 900.0)
    await app.change_quality(720)
    assert app.client.calls[-1]["max_height"] == 720
    item, start = app.player.played[-1]
    assert isinstance(item, Media) and start == 120.0
