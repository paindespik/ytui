"""Textual application: screens, sources, playback orchestration."""

from __future__ import annotations

from textual.app import App
from textual.reactive import reactive

from .cache import MetaCache
from .config import Config, add_channel
from .models import Video
from .player.mpv import MpvController, PlayerError, play
from .sources.base import VideoSource
from .sources.rss import RSSSource
from .thumbnails.fetcher import ThumbnailFetcher
from .ui.screens.channel import ChannelScreen
from .ui.screens.detail import VideoDetailScreen
from .ui.screens.help import HelpScreen
from .ui.screens.history import HistoryScreen
from .ui.screens.home import HomeFeedScreen
from .ui.screens.local_playlists import LocalPlaylistsScreen
from .ui.screens.playlist import PlaylistScreen
from .ui.screens.search import SearchScreen
from .ui.screens.settings import SettingsScreen
from .ui.widgets.modals import PlaylistPickerModal
from .ui.widgets.video_list import VideoList


class YtuiApp(App):
    TITLE = "ytui"

    player_status: reactive[str] = reactive("")

    CSS = """
    #warning-banner {
        width: 100%;
        background: $warning;
        color: $text;
        padding: 0 1;
    }
    #warning-banner.hidden {
        display: none;
    }
    #feed-loading, #search-loading, #channel-loading, #playlist-loading, #detail-loading {
        height: 1;
    }
    Horizontal > VideoList {
        width: 1fr;
    }
    #help-text {
        margin: 2;
        padding: 1 2;
        background: $surface;
        border: round $primary;
        width: auto;
    }
    PlayerBar {
        width: 100%;
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
        display: none;
    }
    PlayerBar.active {
        display: block;
    }
    """

    SCREENS = {
        "home": HomeFeedScreen,
        "search": SearchScreen,
        "help": HelpScreen,
        "history": HistoryScreen,
        "settings": SettingsScreen,
        "local_playlists": LocalPlaylistsScreen,
    }

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.cache = MetaCache()
        self.source: VideoSource = RSSSource(config.channels.list, self.cache)
        self.thumbnails = ThumbnailFetcher()
        self.player = MpvController()

    def on_mount(self) -> None:
        self.push_screen("home")
        # First launch with no channels: go straight to search.
        if not self.config.channels.list:
            self.push_screen("search")
        self.set_interval(2.0, self._poll_player)

    async def on_unmount(self) -> None:
        await self.thumbnails.close()
        await self.player.close()

    # -- playback --

    async def _poll_player(self) -> None:
        self.player.process_alive()  # reap an exited mpv
        status = await self.player.status()
        if status is None:
            self.player_status = ""
            return
        icon = "⏸ paused" if status.paused else "▶ playing"
        queued = f" ({status.queued} queued)" if status.queued else ""
        self.player_status = f"{icon}{queued}"

    def play_video(self, video: Video, audio_only: bool | None = None) -> None:
        self._record_watch(video)
        if audio_only:
            # One-shot audio playback, separate from the controlled queue.
            try:
                play(video.url, self.config.player, audio_only=True)
                self.notify("Playing audio in mpv…", timeout=4)
            except PlayerError as exc:
                self.notify(str(exc), severity="error", timeout=10)
            return
        self.run_worker(self._play_async(video.url), group="player")

    def enqueue_video(self, video: Video) -> None:
        if video.kind == "channel":
            self.notify("Channels cannot be enqueued.", severity="warning", timeout=5)
            return
        self._record_watch(video)
        self.run_worker(self._enqueue_async(video.url), group="player")

    def play_all(self, videos: list[Video]) -> None:
        """Play the first item and append the rest to the mpv queue, in order."""
        playable = [v for v in videos if v.kind != "channel"]
        if not playable:
            self.notify("Nothing to play.", timeout=4)
            return
        for video in playable:
            self._record_watch(video)
        self.run_worker(self._play_all_async([v.url for v in playable]), group="player")

    async def _play_all_async(self, urls: list[str]) -> None:
        try:
            await self.player.play(urls[0], self.config.player)
            for url in urls[1:]:
                await self.player.enqueue(url, self.config.player)
        except PlayerError as exc:
            self.notify(str(exc), severity="error", timeout=10)
            return
        queued = f" ({len(urls) - 1} queued)" if len(urls) > 1 else ""
        self.notify(f"Playing in mpv…{queued}", timeout=4)

    async def _play_async(self, url: str) -> None:
        try:
            await self.player.play(url, self.config.player)
            self.notify("Playing in mpv…", timeout=4)
        except PlayerError as exc:
            self.notify(str(exc), severity="error", timeout=10)

    async def _enqueue_async(self, url: str) -> None:
        try:
            appended = await self.player.enqueue(url, self.config.player)
        except PlayerError as exc:
            self.notify(str(exc), severity="error", timeout=10)
            return
        self.notify("Added to mpv queue." if appended else "Playing in mpv…", timeout=4)

    async def pause_toggle(self) -> None:
        if not await self.player.pause_toggle():
            self.notify("Nothing is playing.", timeout=3)

    async def playlist_next(self) -> None:
        if not await self.player.playlist_next():
            self.notify("Nothing is playing.", timeout=3)

    def _record_watch(self, video: Video) -> None:
        if video.kind == "channel":
            return
        self.cache.record_watch(video)
        self._refresh_watched_markers()

    def _refresh_watched_markers(self) -> None:
        watched = self.cache.watched_ids()
        for screen in self.screen_stack:
            for video_list in screen.query(VideoList):
                video_list.refresh_watched(watched)

    # -- navigation / item actions --

    def open_channel(self, video: Video) -> None:
        """Open the channel of the given item (or the item itself if it is a channel)."""
        if video.kind != "channel" and not video.channel_id:
            self.notify("No channel information for this item.", severity="warning", timeout=5)
            return
        self.push_screen(ChannelScreen(video))

    def open_playlist(self, video: Video) -> None:
        self.push_screen(PlaylistScreen(video))

    def open_detail(self, video: Video) -> None:
        if video.kind != "video":
            self.notify("Details are only available for videos.", timeout=5)
            return
        self.push_screen(VideoDetailScreen(video))

    def add_channel_to_config(self, video: Video) -> None:
        """Persist the item's channel into config.toml [channels].list."""
        channel_id = video.video_id if video.kind == "channel" else video.channel_id
        if channel_id and video.platform == "bitchute":
            channel_id = f"bitchute:{channel_id}"
        if not channel_id:
            self.notify("No channel ID for this item.", severity="warning", timeout=5)
            return
        name = video.channel_title or video.title or channel_id
        if add_channel(channel_id):
            if channel_id not in self.config.channels.list:
                self.config.channels.list.append(channel_id)
            self.notify(f"Added {name} to your channels.", timeout=5)
        else:
            self.notify(f"{name} is already in your channels.", timeout=5)

    def download_video(self, video: Video) -> None:
        if video.kind == "channel":
            self.notify("Channels cannot be downloaded.", severity="warning", timeout=5)
            return
        self.notify(f"Downloading: {video.title}", timeout=5)
        self.run_worker(
            lambda: self._download_blocking(video), thread=True, group="download", exclusive=False
        )

    def _download_blocking(self, video: Video) -> None:
        from .player.download import download_video

        try:
            target = download_video(video.url, self.config.player)
        except Exception as exc:
            self.call_from_thread(
                self.notify, f"Download failed: {exc}", severity="error", timeout=10
            )
            return
        self.call_from_thread(self.notify, f"Downloaded to {target}: {video.title}", timeout=8)

    def save_to_local_playlist(self, video: Video) -> None:
        if video.kind == "channel":
            self.notify("Channels cannot be added to a playlist.", severity="warning", timeout=5)
            return

        def on_picked(playlist_id: int | None) -> None:
            if playlist_id is None:
                return
            if self.cache.add_playlist_item(playlist_id, video):
                self.notify(f"Saved: {video.title}", timeout=4)
            else:
                self.notify("Already in that playlist.", timeout=4)

        self.push_screen(PlaylistPickerModal(), on_picked)
