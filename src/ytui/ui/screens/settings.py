"""Settings screen: followed channels, backend and UI toggles, persisted to config.toml."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, OptionList
from textual.widgets.option_list import Option

from ...config import add_channel, remove_channel, set_option
from ..widgets.modals import ConfirmModal, TextInputModal


class SettingsScreen(Screen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("a", "add_channel", "Add channel"),
        Binding("x", "remove_channel", "Remove channel"),
        Binding("b", "toggle_backend", "Toggle backend"),
        Binding("m", "toggle_audio_only", "Toggle audio-only"),
        Binding("t", "toggle_thumbnails", "Toggle thumbnails"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("question_mark", "help", "Help", show=False),
    ]

    DEFAULT_CSS = """
    SettingsScreen #settings-options {
        padding: 0 1;
        margin-bottom: 1;
        height: auto;
    }
    SettingsScreen #settings-channels-title {
        padding: 0 2;
        text-style: bold;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Label("", id="settings-options")
            yield Label("Followed channels (a = add, x = remove):", id="settings-channels-title")
            yield OptionList(id="settings-channels")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Settings"

    def on_screen_resume(self) -> None:
        self._reload()

    def _reload(self) -> None:
        config = self.app.config
        self.query_one("#settings-options", Label).update(
            f"[b]b[/b] backend: [i]{config.feed.backend}[/i]    "
            f"[b]m[/b] audio only: [i]{config.player.audio_only}[/i]    "
            f"[b]t[/b] thumbnails: [i]{config.ui.thumbnails}[/i]"
        )
        options = self.query_one("#settings-channels", OptionList)
        options.clear_options()
        cache = self.app.cache
        for entry in config.channels.list:
            channel_id = entry
            if entry.startswith("bitchute:"):
                pass  # cached under the full 'bitchute:<name>' key
            elif not entry.startswith("UC"):
                handle = entry if entry.startswith("@") else f"@{entry}"
                channel_id = cache.get_handle(handle) or entry
            name = cache.get_channel_name(channel_id)
            label = f"{name}  [dim]{entry}[/dim]" if name else entry
            options.add_option(Option(label, id=entry))
        if not config.channels.list:
            options.add_option(Option("(no channels followed)", disabled=True))
        else:
            options.highlighted = 0
        options.focus()

    def action_add_channel(self) -> None:
        prompt = (
            "Add channel (UC id, @handle, or bitchute:<slug> for a BitChute channel):"
        )

        def on_entry(entry: str | None) -> None:
            if not entry:
                return
            if add_channel(entry):
                if entry not in self.app.config.channels.list:
                    self.app.config.channels.list.append(entry)
                self.app.notify(f"Added {entry}. Refresh the feed to apply.", timeout=5)
            else:
                self.app.notify(f"{entry} is already in your channels.", timeout=5)
            self._reload()

        self.app.push_screen(TextInputModal(prompt), on_entry)

    def action_remove_channel(self) -> None:
        options = self.query_one("#settings-channels", OptionList)
        index = options.highlighted
        if index is None:
            return
        option = options.get_option_at_index(index)
        if option.id is None:
            return
        entry = option.id

        def on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            remove_channel(entry)
            if entry in self.app.config.channels.list:
                self.app.config.channels.list.remove(entry)
            self.app.notify(f"Removed {entry}. Refresh the feed to apply.", timeout=5)
            self._reload()

        self.app.push_screen(ConfirmModal(f"Stop following {entry!r}?"), on_confirm)

    def action_toggle_backend(self) -> None:
        config = self.app.config
        config.feed.backend = "api" if config.feed.backend == "rss" else "rss"
        set_option("feed", "backend", config.feed.backend)
        if config.feed.backend == "api":
            self.app.notify("Note: the api backend is not implemented yet; rss is used.", timeout=6)
        self._reload()

    def action_toggle_audio_only(self) -> None:
        config = self.app.config
        config.player.audio_only = not config.player.audio_only
        set_option("player", "audio_only", config.player.audio_only)
        self._reload()

    def action_toggle_thumbnails(self) -> None:
        config = self.app.config
        config.ui.thumbnails = not config.ui.thumbnails
        set_option("ui", "thumbnails", config.ui.thumbnails)
        self.app.notify("Thumbnail change applies to newly opened screens.", timeout=5)
        self._reload()

    def action_cursor_down(self) -> None:
        self.query_one("#settings-channels", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#settings-channels", OptionList).action_cursor_up()

    def action_help(self) -> None:
        self.app.push_screen("help")

    def action_go_back(self) -> None:
        self.app.pop_screen()
