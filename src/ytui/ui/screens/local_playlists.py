"""Local playlists: management screen (list/new/rename/delete) and content screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Header, OptionList
from textual.widgets.option_list import Option

from ...cache import LocalPlaylist
from ..widgets.detail_panel import DetailPanel
from ..widgets.modals import ConfirmModal, TextInputModal
from ..widgets.player_bar import PlayerBar
from ..widgets.video_list import VideoList
from .browse import BrowseScreen


class LocalPlaylistsScreen(Screen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("n", "new_playlist", "New"),
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

    def _reload(self) -> None:
        options = self.query_one("#local-playlists", OptionList)
        options.clear_options()
        self._playlists = self.app.cache.list_playlists()
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
            if self.app.cache.create_playlist(name) is None:
                self.app.notify(f"A playlist named {name!r} already exists.", timeout=5)
            self._reload()

        self.app.push_screen(TextInputModal("New playlist name:"), on_name)

    def action_rename_playlist(self) -> None:
        playlist = self._highlighted_playlist()
        if playlist is None:
            return

        def on_name(name: str | None) -> None:
            if not name or name == playlist.name:
                return
            if not self.app.cache.rename_playlist(playlist.id, name):
                self.app.notify(f"A playlist named {name!r} already exists.", timeout=5)
            self._reload()

        self.app.push_screen(TextInputModal("Rename playlist:", initial=playlist.name), on_name)

    def action_delete_playlist(self) -> None:
        playlist = self._highlighted_playlist()
        if playlist is None:
            return

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                self.app.cache.delete_playlist(playlist.id)
                self._reload()

        self.app.push_screen(
            ConfirmModal(f"Delete playlist {playlist.name!r} ({playlist.item_count} items)?"),
            on_confirm,
        )

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

    def _reload(self) -> None:
        video_list = self.query_one("#local-playlist-list", VideoList)
        video_list.set_videos(
            self.app.cache.playlist_items(self.playlist.id), self.app.cache.watched_ids()
        )
        video_list.focus()

    def action_play_all(self) -> None:
        items = self.app.cache.playlist_items(self.playlist.id)
        if not items:
            self.app.notify("This playlist is empty.", timeout=4)
            return
        self.app.play_all(items)

    def action_remove_entry(self) -> None:
        video = self._highlighted()
        if video is None:
            return
        self.app.cache.remove_playlist_item(self.playlist.id, video.video_id)
        self._reload()

    def action_go_back(self) -> None:
        self.app.pop_screen()
