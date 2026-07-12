"""Textual pilot smoke tests: the app boots and the screens open (no network).

The API client is replaced by an in-memory fake covering the endpoints used
by the screens.
"""

from datetime import datetime, timezone

import pytest

from ytui.api_client import FeedResult, FollowedChannel, LocalPlaylist, PlaylistItem
from ytui.config import Config
from ytui.models import Video, VideoDetails

VIDEO = Video(
    video_id="dQw4w9WgXcQ",
    title="A video",
    channel_title="Some Channel",
    channel_id="UC123",
    published=datetime(2024, 1, 1, tzinfo=timezone.utc),
)
PLAYLIST = Video(video_id="PLabc", title="A playlist", kind="playlist")
CHANNEL = Video(video_id="UCabc", title="A channel", kind="channel")


class FakeClient:
    """In-memory stand-in for YtuiClient."""

    def __init__(self) -> None:
        self.base_url = "https://fake"
        self._watched: set[str] = set()
        self._history: list[Video] = []
        self._playlists: dict[int, tuple[str, list[Video]]] = {}
        self._next_playlist_id = 1

    async def close(self) -> None:
        pass

    async def feed(self, refresh: bool = False) -> FeedResult:
        return FeedResult([VIDEO], [])

    async def search(self, query: str, limit: int = 20) -> list[Video]:
        return [VIDEO]

    async def channel_videos(self, channel_id, platform="youtube", limit=50):
        return [VIDEO]

    async def playlist_videos(self, playlist_id, platform="youtube", limit=200):
        return [VIDEO]

    async def video_details(self, video_id, platform="youtube") -> VideoDetails:
        return VideoDetails(description="A description", view_count=1234, duration=60)

    async def channels(self) -> list[FollowedChannel]:
        return [FollowedChannel("@x", "UC123", "Some Channel")]

    async def follow_channel(self, ref: str) -> FollowedChannel:
        return FollowedChannel(ref, "UCnew")

    async def unfollow_channel(self, channel_id: str) -> None:
        pass

    async def history(self, limit: int = 200) -> list[Video]:
        return list(self._history)

    async def record_watch(self, video: Video) -> None:
        self._watched.add(video.video_id)
        self._history.insert(0, video)

    async def watched_ids(self) -> set[str]:
        return set(self._watched)

    async def save_position(self, video_id, position, duration) -> None:
        pass

    async def resume(self, video_id):
        return None

    async def remove_watch(self, video_id: str) -> None:
        self._history = [v for v in self._history if v.video_id != video_id]
        self._watched.discard(video_id)

    async def playlists(self) -> list[LocalPlaylist]:
        return [
            LocalPlaylist(pid, name, 0.0, len(items))
            for pid, (name, items) in self._playlists.items()
        ]

    async def create_playlist(self, name: str) -> int | None:
        if any(n == name for n, _ in self._playlists.values()):
            return None
        pid = self._next_playlist_id
        self._next_playlist_id += 1
        self._playlists[pid] = (name, [])
        return pid

    async def rename_playlist(self, playlist_id: int, name: str) -> bool:
        old_name, items = self._playlists[playlist_id]
        self._playlists[playlist_id] = (name, items)
        return True

    async def delete_playlist(self, playlist_id: int) -> None:
        self._playlists.pop(playlist_id, None)

    async def playlist_items(self, playlist_id: int) -> list[PlaylistItem]:
        _, items = self._playlists.get(playlist_id, ("", []))
        return [PlaylistItem(i, v) for i, v in enumerate(items)]

    async def add_playlist_item(self, playlist_id: int, video: Video) -> bool:
        _, items = self._playlists[playlist_id]
        if any(v.video_id == video.video_id for v in items):
            return False
        items.append(video)
        return True

    async def remove_playlist_item(self, playlist_id: int, position: int) -> None:
        _, items = self._playlists[playlist_id]
        del items[position]

    async def like_video(self, video_id: str) -> None:
        pass

    async def comment_video(self, video_id: str, text: str) -> None:
        pass

    async def lives(self) -> list[Video]:
        return []


@pytest.fixture
def app(tmp_path, monkeypatch):
    # Isolate config/cache (thumbnails) from the real user directories.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    from ytui.app import YtuiApp

    config = Config()
    config.server.url = "https://fake"
    config.server.token = "t"
    app = YtuiApp(config)
    app.client = FakeClient()
    return app


async def test_app_boots_to_home(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.__class__.__name__ == "HomeFeedScreen"


async def test_search_screen_opens(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen("search")
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
        await app.client.record_watch(VIDEO)
        app.push_screen("history")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "HistoryScreen"
        await app.workers.wait_for_complete()
        await pilot.pause()
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
        pid = await app.client.create_playlist("mix")
        await app.client.add_playlist_item(pid, VIDEO)
        app.push_screen("local_playlists")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "LocalPlaylistsScreen"
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "LocalPlaylistContentScreen"
        await app.workers.wait_for_complete()
        await pilot.pause()
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


async def test_q_quits_from_home(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.__class__.__name__ == "HomeFeedScreen"
        await pilot.press("q")
        await pilot.pause()
        assert app._exit
