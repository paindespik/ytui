"""Local playlists (server-side): management screen and content screen."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Header, OptionList
from textual.widgets.option_list import Option

from ...api_client import LocalPlaylist, YtuiApiError
from ..widgets.detail_panel import DetailPanel
from ..widgets.modals import ConfirmModal, TextInputModal
from ..widgets.player_bar import PlayerBar
from ..widgets.video_list import VideoList
from .browse import BrowseScreen


class LocalPlaylistsScreen(Screen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("n", "new_playlist", "New"),
        Binding("i", "import_playlist", "Import YouTube playlist"),
        Binding("r", "rename_playlist", "Rename"),
        Binding("x", "delete_playlist", "Delete"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("question_mark", "help", "Help", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield OptionList(id="local-playlists")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Local playlists"
        self._playlists = []

    def on_screen_resume(self) -> None:
        self._reload()

    @work(exclusive=True, group="reload")
    async def _reload(self) -> None:
        options = self.query_one("#local-playlists", OptionList)
        options.clear_options()
        try:
            self._playlists = await self.app.client.playlists()
        except YtuiApiError as exc:
            self._playlists = []
            options.add_option(Option(f"(server unreachable: {exc.detail})", disabled=True))
            options.focus()
            return
        for playlist in self._playlists:
            label = f"{playlist.name}  ({playlist.item_count} items)"
            options.add_option(Option(label, id=str(playlist.id)))
        if not self._playlists:
            options.add_option(Option("(no local playlists — press n to create one)", disabled=True))
        else:
            options.highlighted = 0
        options.focus()

    def _highlighted_playlist(self) -> LocalPlaylist | None:
        options = self.query_one("#local-playlists", OptionList)
        index = options.highlighted
        if index is None or not self._playlists:
            return None
        option = options.get_option_at_index(index)
        if option.id is None:
            return None
        playlist_id = int(option.id)
        return next((p for p in self._playlists if p.id == playlist_id), None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        playlist_id = int(event.option.id)
        playlist = next((p for p in self._playlists if p.id == playlist_id), None)
        if playlist:
            self.app.push_screen(LocalPlaylistContentScreen(playlist))

    def action_new_playlist(self) -> None:
        def on_name(name: str | None) -> None:
            if not name:
                return
            self._create_playlist(name)

        self.app.push_screen(TextInputModal("New playlist name:"), on_name)

    @work
    async def _create_playlist(self, name: str) -> None:
        try:
            if await self.app.client.create_playlist(name) is None:
                self.app.notify(f"A playlist named {name!r} already exists.", timeout=5)
        except YtuiApiError as exc:
            self.app.notify(f"Could not create: {exc.detail}", severity="error", timeout=8)
        self._reload()

    def action_import_playlist(self) -> None:
        def on_source(source: str | None) -> None:
            if not source:
                return
            self._import_playlist(source)

        self.app.push_screen(
            TextInputModal("Import a YouTube playlist (URL or id):"), on_source
        )

    @work
    async def _import_playlist(self, source: str) -> None:
        self.app.notify("Importing playlist…", timeout=4)
        try:
            result = await self.app.client.import_playlist(source)
        except YtuiApiError as exc:
            detail = exc.detail
            if exc.status_code == 409:
                detail = f"{detail} — rename it or import from the playlist view."
            self.app.notify(f"Import failed: {detail}", severity="error", timeout=8)
            return
        skipped = f", {result.skipped} skipped" if result.skipped else ""
        self.app.notify(
            f"Imported {result.added} videos into {result.playlist.name!r}{skipped}.", timeout=6
        )
        self._reload()

    def action_rename_playlist(self) -> None:
        playlist = self._highlighted_playlist()
        if playlist is None:
            return

        def on_name(name: str | None) -> None:
            if not name or name == playlist.name:
                return
            self._rename_playlist(playlist.id, name)

        self.app.push_screen(TextInputModal("Rename playlist:", initial=playlist.name), on_name)

    @work
    async def _rename_playlist(self, playlist_id: int, name: str) -> None:
        try:
            if not await self.app.client.rename_playlist(playlist_id, name):
                self.app.notify(f"A playlist named {name!r} already exists.", timeout=5)
        except YtuiApiError as exc:
            self.app.notify(f"Could not rename: {exc.detail}", severity="error", timeout=8)
        self._reload()

    def action_delete_playlist(self) -> None:
        playlist = self._highlighted_playlist()
        if playlist is None:
            return

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                self._delete_playlist(playlist.id)

        self.app.push_screen(
            ConfirmModal(f"Delete playlist {playlist.name!r} ({playlist.item_count} items)?"),
            on_confirm,
        )

    @work
    async def _delete_playlist(self, playlist_id: int) -> None:
        try:
            await self.app.client.delete_playlist(playlist_id)
        except YtuiApiError as exc:
            self.app.notify(f"Could not delete: {exc.detail}", severity="error", timeout=8)
        self._reload()

    def action_cursor_down(self) -> None:
        self.query_one("#local-playlists", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#local-playlists", OptionList).action_cursor_up()

    def action_help(self) -> None:
        self.app.push_screen("help")

    def action_go_back(self) -> None:
        self.app.pop_screen()


class LocalPlaylistContentScreen(BrowseScreen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("p", "play_all", "Play all"),
        Binding("x", "remove_entry", "Remove entry"),
    ]

    def __init__(self, playlist: LocalPlaylist) -> None:
        super().__init__()
        self.playlist = playlist
        self._items = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield VideoList(id="local-playlist-list")
            yield DetailPanel(thumbnails_enabled=self.app.config.ui.thumbnails)
        yield PlayerBar()
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f"Playlist: {self.playlist.name}"
        self._reload()

    @work(exclusive=True, group="reload")
    async def _reload(self) -> None:
        try:
            self._items = await self.app.client.playlist_items(self.playlist.id)
        except YtuiApiError as exc:
            self.app.notify(f"Failed to load playlist: {exc.detail}", severity="error", timeout=8)
            return
        video_list = self.query_one("#local-playlist-list", VideoList)
        video_list.set_videos([item.video for item in self._items], self.app.watched)
        video_list.focus()

    def action_play_all(self) -> None:
        if not self._items:
            self.app.notify("This playlist is empty.", timeout=4)
            return
        self.app.play_all([item.video for item in self._items])

    def action_remove_entry(self) -> None:
        video = self._highlighted()
        if video is None:
            return
        item = next((i for i in self._items if i.video.video_id == video.video_id), None)
        if item is None:
            return
        self._remove_item(item.position)

    @work
    async def _remove_item(self, position: int) -> None:
        try:
            await self.app.client.remove_playlist_item(self.playlist.id, position)
        except YtuiApiError as exc:
            self.app.notify(f"Could not remove: {exc.detail}", severity="error", timeout=8)
        self._reload()

    def action_go_back(self) -> None:
        self.app.pop_screen()
