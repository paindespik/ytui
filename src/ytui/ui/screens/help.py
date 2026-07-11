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
  q          Quit (home)
  ?          This help
  Escape     Close / go back

[b]Lists (feed, search, channel, playlist, history)[/b]
  j / k / ↓ / ↑   Move selection
  g / G           Top / bottom
  Enter           Play video/playlist in mpv, or open a channel
  e               Enqueue in the running mpv (append-play)
  i               Video details (full description, views, likes)
  o               Open the channel or playlist of the item
  a               Follow the channel (add to config)
  A               Play audio only (this video)
  d               Download to [player].download_dir
  s               Save to a local playlist
  Space           Pause / resume mpv
  n               Next in mpv queue

[b]Home feed[/b]
  r               Refresh feed
  /               Open search
  h               Watch history
  P               Local playlists
  ,               Settings

[b]Search[/b]
  Type a query, Enter to search
  Results include videos, playlists and channels
  /               Focus the search input

[b]Playlist view (YouTube)[/b]
  Enter           Play one video
  p               Play the whole playlist

[b]Watch history[/b]
  Enter           Replay
  x               Remove entry

[b]Local playlists[/b]
  n / r / x       New / rename / delete playlist
  Enter           Open the playlist
  In a playlist:  Enter play entry, p play all (mpv queue), x remove

[b]Video details[/b]
  Enter play · A audio only · e enqueue · y copy URL
  o open channel · d download · s save to playlist

[b]Settings[/b]
  x remove channel · b backend · m audio-only · t thumbnails

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
