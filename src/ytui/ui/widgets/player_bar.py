"""One-line playback status bar shown while the controlled mpv instance runs."""

from __future__ import annotations

from textual.widgets import Static


class PlayerBar(Static):
    """Watches the app's player_status reactive; hidden when nothing plays."""

    def on_mount(self) -> None:
        self.watch(self.app, "player_status", self._on_status)

    def _on_status(self, status: str) -> None:
        self.update(status)
        self.set_class(bool(status), "active")
