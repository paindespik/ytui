"""Textual application: screens, sources, playback orchestration."""

from __future__ import annotations

from textual.app import App

from .cache import MetaCache
from .config import Config
from .player.mpv import PlayerError, play
from .sources.base import VideoSource
from .sources.rss import RSSSource
from .ui.screens.help import HelpScreen
from .ui.screens.home import HomeFeedScreen
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
    #feed-loading, #search-loading {
        height: 1;
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

    def on_mount(self) -> None:
        self.push_screen("home")
        # First launch with no channels: go straight to search.
        if not self.config.channels.list:
            self.push_screen("search")

    def play_video(self, url: str) -> None:
        try:
            play(url, self.config.player)
            self.notify("Playing in mpv…", timeout=4)
        except PlayerError as exc:
            self.notify(str(exc), severity="error", timeout=10)
