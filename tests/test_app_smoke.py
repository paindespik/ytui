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
    import ytui.ui.screens.detail as detail_mod
    import ytui.ui.screens.playlist as playlist_mod
    from ytui.sources.ytdlp_source import VideoDetails

    monkeypatch.setattr(channel_mod, "channel_videos", lambda url, limit=50: [VIDEO])
    monkeypatch.setattr(playlist_mod, "playlist_videos", lambda url, limit=200: [VIDEO])
    monkeypatch.setattr(
        detail_mod,
        "video_details",
        lambda url: VideoDetails(description="A description", view_count=1234, duration=60),
    )

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


async def test_history_screen_opens(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        app.cache.record_watch(VIDEO)
        app.push_screen("history")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "HistoryScreen"
        from ytui.ui.widgets.video_list import VideoList

        assert app.screen.query_one("#history-list", VideoList).video_at_cursor().video_id == (
            VIDEO.video_id
        )


async def test_settings_screen_opens(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen("settings")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "SettingsScreen"


async def test_local_playlists_screen_opens(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        pid = app.cache.create_playlist("mix")
        app.cache.add_playlist_item(pid, VIDEO)
        app.push_screen("local_playlists")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "LocalPlaylistsScreen"
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "LocalPlaylistContentScreen"
        from ytui.ui.widgets.video_list import VideoList

        video_list = app.screen.query_one("#local-playlist-list", VideoList)
        assert video_list.video_at_cursor().video_id == VIDEO.video_id


async def test_detail_screen_opens(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        app.open_detail(VIDEO)
        await pilot.pause()
        assert app.screen.__class__.__name__ == "VideoDetailScreen"
        await app.workers.wait_for_complete()
        await pilot.pause()
        from textual.widgets import Static

        description = app.screen.query_one("#detail-description", Static)
        assert "A description" in str(description.render())


async def test_playlist_picker_modal_opens(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        app.save_to_local_playlist(VIDEO)
        await pilot.pause()
        assert app.screen.__class__.__name__ == "PlaylistPickerModal"
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "PlaylistPickerModal"
