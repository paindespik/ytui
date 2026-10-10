"""Channel view: videos of one channel (newest or most viewed first), its
playlists (YouTube, BitChute) in a second tab, with 'follow channel' action."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import (
    Footer,
    Header,
    Input,
    LoadingIndicator,
    TabbedContent,
    TabPane,
)

from ...api_client import YtuiApiError
from ...models import Video
from ..widgets.detail_panel import DetailPanel
from ..widgets.player_bar import PlayerBar
from ..widgets.video_list import VideoList
from .browse import BrowseScreen

# Platforms whose channels list their playlists (GET /channels/{id}/playlists).
PLAYLIST_PLATFORMS = ("youtube", "bitchute")
# Platforms whose video listing can be ordered by views (sort=views).
SORT_PLATFORMS = ("youtube", "bitchute", "crowdbunker", "odysee")
SORT_LABELS = {"date": "newest first", "views": "most viewed"}

VIDEOS_TAB = "tab-videos"
PLAYLISTS_TAB = "tab-playlists"


class ChannelScreen(BrowseScreen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("slash", "search", "Search"),
        Binding("a", "add_this_channel", "Follow channel"),
        Binding("m", "load_more", "Load more"),
        Binding("t", "toggle_tab", "Videos/Playlists"),
        Binding("O", "toggle_sort", "Sort date/views"),
    ]

    DEFAULT_CSS = """
    ChannelScreen #channel-tabs {
        width: 1fr;
        height: 1fr;
    }
    ChannelScreen #channel-tabs > ContentSwitcher {
        height: 1fr;
    }
    ChannelScreen #channel-tabs TabPane {
        height: 1fr;
        padding: 0;
    }
    ChannelScreen #channel-tabs VideoList {
        height: 1fr;
    }
    """

    PAGE_SIZE = 50

    def __init__(self, channel: Video) -> None:
        super().__init__()
        self.channel = channel
        self._loaded: list[Video] = []
        self._has_more = False
        self._loading_page = False
        self._query: str | None = None
        # Listing order: "date" (newest first) or "views" (most viewed first).
        self._sort = "date"
        self._is_live = False
        self._videos_shown = False
        # Playlists tab: fetched on first activation only.
        self._playlists: list[Video] = []
        self._playlists_has_more = False
        self._playlists_requested = False
        self._playlists_done = False
        self._loading_playlists_page = False

    @property
    def channel_ref(self) -> str:
        return (
            self.channel.video_id if self.channel.kind == "channel" else self.channel.channel_id
        )

    @property
    def has_playlists(self) -> bool:
        return self.channel.platform in PLAYLIST_PLATFORMS

    @property
    def can_sort(self) -> bool:
        return self.channel.platform in SORT_PLATFORMS

    @property
    def listing_sort(self) -> str:
        """The order asked from the server: a search keeps its own order."""
        return "date" if self._query else self._sort

    @property
    def on_playlists_tab(self) -> bool:
        if not self.has_playlists:
            return False
        return self.query_one("#channel-tabs", TabbedContent).active == PLAYLISTS_TAB

    def compose(self) -> ComposeResult:
        yield Header()
        search = Input(placeholder="Search this channel…", id="channel-search")
        search.display = False
        yield search
        yield LoadingIndicator(id="channel-loading")
        with Horizontal():
            if self.has_playlists:
                with TabbedContent(id="channel-tabs", initial=VIDEOS_TAB):
                    with TabPane("Videos", id=VIDEOS_TAB):
                        yield VideoList(id="channel-list")
                    with TabPane("Playlists", id=PLAYLISTS_TAB):
                        yield VideoList(id="channel-playlists", mark_watched=False)
            else:
                yield VideoList(id="channel-list")
            yield DetailPanel(thumbnails_enabled=self.app.config.ui.thumbnails)
        yield PlayerBar()
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.channel.channel_title or self.channel.title or "Channel"
        if self.channel.platform == "tiktok":
            # TikTok video pages are not listable without auth: lives only.
            self.app.notify(
                "Chaîne en direct uniquement — voir l'onglet Lives", timeout=8
            )
        self.load_videos()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "toggle_tab" and not self.has_playlists:
            return False  # no Playlists tab on this platform: hide the key
        if action == "toggle_sort" and not self.can_sort:
            return False  # no views order upstream: hide the key
        return True

    # -- videos tab --

    @work(exclusive=True, group="reload")
    async def load_videos(self) -> None:
        try:
            videos, has_more = await self.app.client.channel_videos(
                self.channel_ref,
                platform=self.channel.platform,
                limit=self.PAGE_SIZE,
                q=self._query,
                sort=self.listing_sort,
            )
        except YtuiApiError as exc:
            self._show_error(exc.detail)
            return
        self._loaded = list(videos)
        self._has_more = has_more
        self._is_live = await self._channel_is_live()
        self._show_videos(videos)

    async def _channel_is_live(self) -> bool:
        try:
            lives = await self.app.client.lives()
        except YtuiApiError:
            return False
        return any(v.channel_id == self.channel_ref for v in lives)

    def action_load_more(self) -> None:
        if self.on_playlists_tab:
            self._load_more_playlists()
            return
        if self._loading_page:
            self.app.notify("Already loading…", timeout=2)
            return
        if not self._has_more:
            self.app.notify("No more videos", timeout=2)
            return
        # Set synchronously: the worker only starts on the next event loop turn,
        # so a second keypress would otherwise slip past the guard.
        self._loading_page = True
        self.load_more_videos()

    @work(exclusive=True, group="channel-page")
    async def load_more_videos(self) -> None:
        loading = self.query_one("#channel-loading", LoadingIndicator)
        loading.display = True
        listing = (self._query, self._sort)
        try:
            videos, has_more = await self.app.client.channel_videos(
                self.channel_ref,
                platform=self.channel.platform,
                limit=self.PAGE_SIZE,
                offset=len(self._loaded),
                q=self._query,
                sort=self.listing_sort,
            )
        except YtuiApiError as exc:
            self.app.notify(f"Failed to load more: {exc.detail}", severity="error", timeout=8)
            return
        finally:
            self._loading_page = False
            loading.display = False
        if listing != (self._query, self._sort):
            return  # a new search or order restarted the listing meanwhile
        self._has_more = has_more
        self._loaded.extend(videos)
        video_list = self.query_one("#channel-list", VideoList)
        row = video_list.cursor_row
        video_list.append_videos(videos, self.app.watched)
        if 0 <= row < len(self._loaded):
            video_list.move_cursor(row=row)
        self._update_sub_title()

    def _show_error(self, message: str) -> None:
        self.query_one("#channel-loading", LoadingIndicator).display = False
        self.app.notify(f"Failed to load channel: {message}", severity="error", timeout=8)

    def _show_videos(self, videos: list[Video]) -> None:
        self.query_one("#channel-loading", LoadingIndicator).display = False
        video_list = self.query_one("#channel-list", VideoList)
        video_list.set_videos(list(videos), self.app.watched)
        self._videos_shown = True
        if not self.on_playlists_tab:  # never steal focus into a hidden pane
            video_list.focus()
        self._update_sub_title()

    # -- playlists tab --

    def action_toggle_tab(self) -> None:
        if not self.has_playlists:
            return
        tabs = self.query_one("#channel-tabs", TabbedContent)
        tabs.active = VIDEOS_TAB if tabs.active == PLAYLISTS_TAB else PLAYLISTS_TAB

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        event.stop()
        if event.pane.id == PLAYLISTS_TAB:
            if not self._playlists_requested:
                self._playlists_requested = True
                self.query_one("#channel-loading", LoadingIndicator).display = True
                self.load_playlists()
            self.query_one("#channel-playlists", VideoList).focus()
        else:
            self.query_one("#channel-list", VideoList).focus()
        self._show_highlighted()
        self._update_sub_title()

    def _show_highlighted(self) -> None:
        """The detail panel follows the list of the tab just shown."""
        video = self._highlighted()
        if video is not None:
            for panel in self.query(DetailPanel):
                panel.show_video(video)

    @work(exclusive=True, group="playlists")
    async def load_playlists(self) -> None:
        try:
            playlists, has_more = await self.app.client.channel_playlists(
                self.channel_ref, platform=self.channel.platform, limit=self.PAGE_SIZE
            )
        except YtuiApiError as exc:
            # Asked again on the next visit of the tab.
            self._playlists_requested = False
            self.query_one("#channel-loading", LoadingIndicator).display = False
            self.app.notify(
                f"Failed to load playlists: {exc.detail}", severity="error", timeout=8
            )
            return
        self.query_one("#channel-loading", LoadingIndicator).display = False
        self._playlists = list(playlists)
        self._playlists_has_more = has_more
        self._playlists_done = True
        playlist_list = self.query_one("#channel-playlists", VideoList)
        playlist_list.set_videos(list(playlists))
        if self.on_playlists_tab:
            playlist_list.focus()
            self._show_highlighted()
        if not playlists:
            self.app.notify("This channel has no playlists", timeout=4)
        self._update_sub_title()

    def _load_more_playlists(self) -> None:
        if self._loading_playlists_page or not self._playlists_done:
            self.app.notify("Already loading…", timeout=2)
            return
        if not self._playlists_has_more:
            self.app.notify("No more playlists", timeout=2)
            return
        self._loading_playlists_page = True  # synchronous guard, as for videos
        self.load_more_playlists()

    @work(exclusive=True, group="playlists-page")
    async def load_more_playlists(self) -> None:
        loading = self.query_one("#channel-loading", LoadingIndicator)
        loading.display = True
        try:
            playlists, has_more = await self.app.client.channel_playlists(
                self.channel_ref,
                platform=self.channel.platform,
                limit=self.PAGE_SIZE,
                offset=len(self._playlists),
            )
        except YtuiApiError as exc:
            self.app.notify(f"Failed to load more: {exc.detail}", severity="error", timeout=8)
            return
        finally:
            self._loading_playlists_page = False
            loading.display = False
        self._playlists_has_more = has_more
        self._playlists.extend(playlists)
        playlist_list = self.query_one("#channel-playlists", VideoList)
        row = playlist_list.cursor_row
        playlist_list.append_videos(playlists)
        if 0 <= row < len(self._playlists):
            playlist_list.move_cursor(row=row)
        self._update_sub_title()

    def on_video_list_video_selected(self, event: VideoList.VideoSelected) -> None:
        # In the Playlists tab, Enter browses the playlist (like the Music
        # view), whose screen plays it whole with 'p'. Everything else falls through to
        # BrowseScreen's handler, which Textual runs after this one unless
        # the default is prevented.
        if event.video.kind == "playlist" and self.on_playlists_tab:
            event.prevent_default()
            self.app.open_playlist(event.video)

    def _highlighted(self) -> Video | None:
        if self.has_playlists:
            list_id = "#channel-playlists" if self.on_playlists_tab else "#channel-list"
            return self.query_one(list_id, VideoList).video_at_cursor()
        return super()._highlighted()

    # -- header --

    def _update_sub_title(self) -> None:
        name = self.channel.channel_title or self.channel.title or "Channel"
        if self._is_live:
            name = f"● LIVE — {name}"
        if self.on_playlists_tab:
            if not self._playlists_done:
                self.sub_title = f"{name} — playlists…"
                return
            suffix = "" if self._playlists_has_more else " (end)"
            self.sub_title = f"{name} — {len(self._playlists)} playlists{suffix}"
            return
        if not self._videos_shown:  # first page still loading
            self.sub_title = name
            return
        suffix = "" if self._has_more else " (end)"
        count = len(self._loaded)
        if self._query:
            self.sub_title = f"{name} — {count} results for '{self._query}'{suffix}"
        elif self._sort != "date":
            order = SORT_LABELS[self._sort]
            self.sub_title = f"{name} — {count} videos, {order}{suffix}"
        else:
            self.sub_title = f"{name} — {count} videos{suffix}"

    def action_toggle_sort(self) -> None:
        """Switch the videos listing between newest and most viewed first."""
        if not self.can_sort:
            return
        if self._query:
            self.app.notify(
                "Search results keep their own order — Escape clears the search",
                timeout=4,
            )
            return
        if self.on_playlists_tab:
            # The order applies to the videos: show the tab it reorders.
            self.query_one("#channel-tabs", TabbedContent).active = VIDEOS_TAB
        self._sort = "views" if self._sort == "date" else "date"
        self.app.notify(f"Videos: {SORT_LABELS[self._sort]}", timeout=3)
        self._restart_listing()

    def action_add_this_channel(self) -> None:
        self.app.follow_channel(self.channel)

    def action_go_back(self) -> None:
        search = self.query_one("#channel-search", Input)
        if search.display:
            # Escape leaves the search first, the screen second.
            search.display = False
            search.value = ""
            self.query_one("#channel-list", VideoList).focus()
            if self._query is not None:
                self._set_query(None)
            return
        self.app.pop_screen()

    def action_search(self) -> None:
        if self.on_playlists_tab:
            # The search looks for videos: show the tab it fills.
            self.query_one("#channel-tabs", TabbedContent).active = VIDEOS_TAB
        search = self.query_one("#channel-search", Input)
        search.display = True
        search.value = self._query or ""
        search.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "channel-search":
            return
        query = event.value.strip() or None
        if query is None:
            event.input.display = False
            if self._query is None:  # nothing searched yet: keep the listing as is
                self.query_one("#channel-list", VideoList).focus()
                return
        self._set_query(query)

    def _set_query(self, query: str | None) -> None:
        """Restart the listing from page one, searching when a query is given."""
        self._query = query
        self._restart_listing()

    def _restart_listing(self) -> None:
        self._loaded = []
        self._has_more = False
        self.query_one("#channel-loading", LoadingIndicator).display = True
        self.load_videos()  # focuses the list once the first page lands
