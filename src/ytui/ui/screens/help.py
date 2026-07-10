"""Help screen: keybindings overview."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center
from textual.screen import ModalScreen
from textual.widgets import Static

HELP_TEXT = """\
[b]ytui — keybindings[/b]

[b]Global[/b]
  q          Quit
  ?          This help
  Escape     Close / go back

[b]Lists (feed, search, channel, playlist)[/b]
  j / k / ↓ / ↑   Move selection
  g / G           Top / bottom
  Enter           Play video/playlist in mpv, or open a channel
  o               Open the channel or playlist of the item
  a               Follow the channel (add to config)

[b]Home feed[/b]
  r               Refresh feed
  /               Open search

[b]Search[/b]
  Type a query, Enter to search
  Results include videos, playlists and channels
  /               Focus the search input

[b]Playlist view[/b]
  Enter           Play one video
  p               Play the whole playlist

The panel on the right shows the highlighted item's thumbnail
(sixel/kitty/half-block; disable with ui.thumbnails = false).

Press any key to close."""


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,q,question_mark", "dismiss_help", "Close")]

    def compose(self) -> ComposeResult:
        with Center():
            yield Static(HELP_TEXT, id="help-text")

    def on_key(self) -> None:
        self.app.pop_screen()

    def action_dismiss_help(self) -> None:
        self.app.pop_screen()
