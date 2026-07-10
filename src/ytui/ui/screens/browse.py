"""Shared base screen for video lists: detail panel, playback and item actions."""

from __future__ import annotations

from textual.binding import Binding
from textual.screen import Screen

from ...models import Video
from ..widgets.detail_panel import DetailPanel
from ..widgets.video_list import VideoList


class BrowseScreen(Screen):
    """Common behaviour for screens showing a VideoList next to a DetailPanel."""

    BINDINGS = [
        Binding("o", "open_item", "Open channel/playlist"),
        Binding("a", "add_channel", "Follow channel"),
        Binding("question_mark", "help", "Help", show=False),
    ]

    def action_help(self) -> None:
        self.app.push_screen("help")

    def _highlighted(self) -> Video | None:
        lists = self.query(VideoList)
        for video_list in lists:
            if video_list.has_focus:
                return video_list.video_at_cursor()
        return lists.first().video_at_cursor() if lists else None

    def on_video_list_video_highlighted(self, event: VideoList.VideoHighlighted) -> None:
        for panel in self.query(DetailPanel):
            panel.show_video(event.video)

    def on_video_list_video_selected(self, event: VideoList.VideoSelected) -> None:
        video = event.video
        if video.kind == "channel":
            self.app.open_channel(video)
        else:
            # Videos and playlists both play directly; mpv's ytdl_hook
            # handles playlist URLs natively.
            self.app.play_video(video.url)

    def action_open_item(self) -> None:
        video = self._highlighted()
        if video is None:
            return
        if video.kind == "playlist":
            self.app.open_playlist(video)
        else:
            self.app.open_channel(video)

    def action_add_channel(self) -> None:
        video = self._highlighted()
        if video is None:
            return
        self.app.add_channel_to_config(video)
