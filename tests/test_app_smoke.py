"""Textual pilot smoke tests: the app boots and the new screens open (no network)."""

from datetime import datetime, timezone

import pytest

from ytui.config import Config
from ytui.models import Video

VIDEO = Video(
    video_id="dQw4w9WgXcQ",
    title="A video",
    channel_title="Some Channel",
    channel_id="UC123",
    published=datetime(2024, 1, 1, tzinfo=timezone.utc),
)
PLAYLIST = Video(video_id="PLabc", title="A playlist", kind="playlist")
CHANNEL = Video(video_id="UCabc", title="A channel", kind="channel")


@pytest.fixture
def app(tmp_path, monkeypatch):
    # Isolate config/cache and disable network-touching pieces.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    import ytui.ui.screens.channel as channel_mod
    import ytui.ui.screens.playlist as playlist_mod

    monkeypatch.setattr(channel_mod, "channel_videos", lambda url, limit=50: [VIDEO])
    monkeypatch.setattr(playlist_mod, "playlist_videos", lambda url, limit=200: [VIDEO])

    from ytui.app import YtuiApp

    config = Config()
    config.channels.list = []
    return YtuiApp(config)


async def test_app_boots_to_search_when_no_channels(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.__class__.__name__ == "SearchScreen"


async def test_channel_screen_opens(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        app.open_channel(CHANNEL)
        await pilot.pause()
        assert app.screen.__class__.__name__ == "ChannelScreen"
        await app.workers.wait_for_complete()
        await pilot.pause()
        from ytui.ui.widgets.video_list import VideoList

        assert app.screen.query_one("#channel-list", VideoList).video_at_cursor() == VIDEO


async def test_playlist_screen_opens(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        app.open_playlist(PLAYLIST)
        await pilot.pause()
        assert app.screen.__class__.__name__ == "PlaylistScreen"
        await app.workers.wait_for_complete()
        await pilot.pause()
        from ytui.ui.widgets.video_list import VideoList

        assert app.screen.query_one("#playlist-list", VideoList).video_at_cursor() == VIDEO


async def test_help_screen_opens(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen("help")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "HelpScreen"
