"""Textual application: screens, sources, playback orchestration."""

from __future__ import annotations

from textual.app import App

from .cache import MetaCache
from .config import Config, add_channel
from .models import Video
from .player.mpv import PlayerError, play
from .sources.base import VideoSource
from .sources.rss import RSSSource
from .thumbnails.fetcher import ThumbnailFetcher
from .ui.screens.channel import ChannelScreen
from .ui.screens.help import HelpScreen
from .ui.screens.home import HomeFeedScreen
from .ui.screens.playlist import PlaylistScreen
from .ui.screens.search import SearchScreen


class YtuiApp(App):
    TITLE = "ytui"

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
    #feed-loading, #search-loading, #channel-loading, #playlist-loading {
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
    """

    SCREENS = {
        "home": HomeFeedScreen,
        "search": SearchScreen,
        "help": HelpScreen,
    }

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.cache = MetaCache()
        self.source: VideoSource = RSSSource(config.channels.list, self.cache)
        self.thumbnails = ThumbnailFetcher()

    def on_mount(self) -> None:
        self.push_screen("home")
        # First launch with no channels: go straight to search.
        if not self.config.channels.list:
            self.push_screen("search")

    async def on_unmount(self) -> None:
        await self.thumbnails.close()

    def play_video(self, url: str) -> None:
        try:
            play(url, self.config.player)
            self.notify("Playing in mpv…", timeout=4)
        except PlayerError as exc:
            self.notify(str(exc), severity="error", timeout=10)

    def open_channel(self, video: Video) -> None:
        """Open the channel of the given item (or the item itself if it is a channel)."""
        if video.kind != "channel" and not video.channel_id:
            self.notify("No channel information for this item.", severity="warning", timeout=5)
            return
        self.push_screen(ChannelScreen(video))

    def open_playlist(self, video: Video) -> None:
        self.push_screen(PlaylistScreen(video))

    def add_channel_to_config(self, video: Video) -> None:
        """Persist the item's channel into config.toml [channels].list."""
        channel_id = video.video_id if video.kind == "channel" else video.channel_id
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
