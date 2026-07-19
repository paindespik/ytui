"""Small modal dialogs: text input, confirm, local-playlist picker."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

_MODAL_CSS = """
TextInputModal, ConfirmModal, PlaylistPickerModal {
    align: center middle;
}
TextInputModal > Vertical, ConfirmModal > Vertical, PlaylistPickerModal > Vertical {
    width: 60;
    height: auto;
    max-height: 20;
    padding: 1 2;
    background: $surface;
    border: round $primary;
}
"""


class TextInputModal(ModalScreen[str | None]):
    """Prompt for one line of text; dismisses with the text or None on escape."""

    CSS = _MODAL_CSS
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, initial: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._prompt)
            yield Input(value=self._initial, id="modal-input")

    def on_mount(self) -> None:
        self.query_one("#modal-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.dismiss(text or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """y/n confirmation; dismisses with True or False."""

    CSS = _MODAL_CSS
    BINDINGS = [
        Binding("y,enter", "confirm", "Yes"),
        Binding("escape,n", "cancel", "No"),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._prompt)
            yield Label("[b]y[/b]/Enter = yes    [b]n[/b]/Escape = no")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class PlaylistPickerModal(ModalScreen[int | None]):
    """Pick a local playlist (or create one); dismisses with its id or None."""

    CSS = _MODAL_CSS
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("n", "new_playlist", "New playlist"),
    ]

    _NEW_ID = "__new__"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Save to local playlist ([b]n[/b] = new, Escape = cancel)")
            yield OptionList(id="playlist-options")

    def on_mount(self) -> None:
        self._load_playlists()

    @work(exclusive=True)
    async def _load_playlists(self) -> None:
        from ...api_client import YtuiApiError

        options = self.query_one("#playlist-options", OptionList)
        try:
            playlists = await self.app.client.playlists()
        except YtuiApiError as exc:
            self.app.notify(
                f"Playlists unavailable: {exc.detail}", severity="error", timeout=8
            )
            playlists = []
        for playlist in playlists:
            options.add_option(
                Option(f"{playlist.name} ({playlist.item_count})", id=str(playlist.id))
            )
        options.add_option(Option("+ New playlist…", id=self._NEW_ID))
        options.highlighted = 0
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == self._NEW_ID:
            self.action_new_playlist()
        else:
            self.dismiss(int(event.option.id))

    def action_new_playlist(self) -> None:
        def on_name(name: str | None) -> None:
            if not name:
                return
            self._create_and_dismiss(name)

        self.app.push_screen(TextInputModal("New playlist name:"), on_name)

    @work
    async def _create_and_dismiss(self, name: str) -> None:
        from ...api_client import YtuiApiError

        try:
            playlist_id = await self.app.client.create_playlist(name)
            if playlist_id is None:
                existing = next(
                    (p for p in await self.app.client.playlists() if p.name == name), None
                )
                playlist_id = existing.id if existing else None
        except YtuiApiError as exc:
            self.app.notify(f"Could not create: {exc.detail}", severity="error", timeout=8)
            playlist_id = None
        self.dismiss(playlist_id)

    def action_cancel(self) -> None:
        self.dismiss(None)
