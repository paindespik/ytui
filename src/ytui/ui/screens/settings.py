"""Settings screen: followed channels (server-side) and local UI/player toggles."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, OptionList
from textual.widgets.option_list import Option

from ...api_client import FollowedChannel, YtuiApiError
from ...config import set_option
from ...models import Video
from ..widgets.modals import ConfirmModal, TextInputModal


class SettingsScreen(Screen):
    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("a", "add_channel", "Add channel"),
        Binding("x", "remove_channel", "Remove channel"),
        Binding("m", "toggle_audio_only", "Toggle audio-only"),
        Binding("t", "toggle_thumbnails", "Toggle thumbnails"),
        Binding("Q", "quality", "Max height"),
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
            yield Label(
                "Followed channels (enter = open, a = add, x = remove):",
                id="settings-channels-title",
            )
            yield OptionList(id="settings-channels")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Settings"
        self._channels: list[FollowedChannel] = []

    def on_screen_resume(self) -> None:
        self._reload()

    # Own group: an exclusive reload in the default group would cancel the
    # _add_channel/_remove_channel workers when the modal pops and the screen
    # resumes (the request died mid-flight — channels never got removed).
    @work(exclusive=True, group="reload")
    async def _reload(self) -> None:
        config = self.app.config
        server = config.server.url or "(not configured)"
        self.query_one("#settings-options", Label).update(
            f"server: [i]{server}[/i]    "
            f"[b]Q[/b] max height: [i]{config.player.max_height}p[/i]    "
            f"[b]m[/b] audio only: [i]{config.player.audio_only}[/i]    "
            f"[b]t[/b] thumbnails: [i]{config.ui.thumbnails}[/i]"
        )
        options = self.query_one("#settings-channels", OptionList)
        options.clear_options()
        try:
            self._channels = await self.app.client.channels()
        except YtuiApiError as exc:
            self._channels = []
            options.add_option(Option(f"(server unreachable: {exc.detail})", disabled=True))
            options.focus()
            return
        for channel in self._channels:
            label = f"{channel.title}  [dim]{channel.ref}[/dim]" if channel.title else channel.ref
            if channel.platform == "twitch":
                label = f"[#a970ff]Twitch[/] · {label}"
            elif channel.platform == "tiktok":
                label = f"[#ff0050]TikTok[/] · {label}"
            elif channel.platform == "crowdbunker":
                label = f"[#e09b3d]CrowdBunker[/] · {label}"
            options.add_option(Option(label, id=channel.channel_id))
        if not self._channels:
            options.add_option(Option("(no channels followed)", disabled=True))
        else:
            options.highlighted = 0
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        channel = next((c for c in self._channels if c.channel_id == event.option.id), None)
        if channel is None:
            return
        self.app.open_channel(
            Video(
                video_id=channel.channel_id,
                title=channel.title or channel.ref,
                channel_title=channel.title,
                kind="channel",
                platform=channel.platform,
            )
        )

    def action_add_channel(self) -> None:
        prompt = (
            "Add channel (UC id, @handle, bitchute:<slug>, odysee:@name:claim, "
            "twitch:<login>, tiktok:<user> or crowdbunker:<handle>):"
        )

        def on_entry(entry: str | None) -> None:
            if not entry:
                return
            self._add_channel(entry)

        self.app.push_screen(TextInputModal(prompt), on_entry)

    @work
    async def _add_channel(self, entry: str) -> None:
        try:
            channel = await self.app.client.follow_channel(entry)
        except YtuiApiError as exc:
            if exc.status_code == 409:
                self.app.notify(f"{entry} is already in your channels.", timeout=5)
            else:
                self.app.notify(f"Could not add {entry}: {exc.detail}", severity="error", timeout=8)
            return
        name = channel.title or entry
        if channel.platform == "twitch":
            self.app.notify(
                f"Added {name} (Twitch). Lives pin to the top of Home when the "
                "channel goes live \U0001f7e3 — Twitch adds no videos to the feed.",
                timeout=8,
            )
        elif channel.platform == "tiktok":
            self.app.notify(
                f"Added {name} (TikTok). Lives pin to the top of Home when the "
                "channel goes live \U0001f3b5 — TikTok adds no videos to the feed.",
                timeout=8,
            )
        else:
            self.app.notify(f"Added {name}. Refresh the feed to apply.", timeout=5)
        self._reload()

    def action_remove_channel(self) -> None:
        options = self.query_one("#settings-channels", OptionList)
        index = options.highlighted
        if index is None:
            return
        option = options.get_option_at_index(index)
        if option.id is None:
            return
        channel = next((c for c in self._channels if c.channel_id == option.id), None)
        if channel is None:
            return

        def on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            self._remove_channel(channel)

        label = channel.title or channel.ref
        self.app.push_screen(ConfirmModal(f"Stop following {label!r}?"), on_confirm)

    @work
    async def _remove_channel(self, channel: FollowedChannel) -> None:
        try:
            await self.app.client.unfollow_channel(channel.channel_id)
        except YtuiApiError as exc:
            self.app.notify(f"Could not remove: {exc.detail}", severity="error", timeout=8)
            return
        self.app.notify(f"Removed {channel.ref}. Refresh the feed to apply.", timeout=5)
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

    def action_quality(self) -> None:
        self.app.pick_quality()

    def action_cursor_down(self) -> None:
        self.query_one("#settings-channels", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#settings-channels", OptionList).action_cursor_up()

    def action_help(self) -> None:
        self.app.push_screen("help")

    def action_go_back(self) -> None:
        self.app.pop_screen()
