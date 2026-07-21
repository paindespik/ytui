"""Textual application: screens, API client, playback orchestration."""

from __future__ import annotations

import time

from textual.app import App
from textual.reactive import reactive

from .api_client import YtuiApiError, YtuiClient, resume_start
from .config import Config
from .livenotify import send_live_notification
from .models import SponsorSegment, Video, video_id_from_url
from .player.mpv import MpvController, PlayerError, play
from .thumbnails.fetcher import ThumbnailFetcher
from .ui.screens.channel import ChannelScreen
from .ui.screens.detail import VideoDetailScreen
from .ui.screens.help import HelpScreen
from .ui.screens.history import HistoryScreen
from .ui.screens.home import HomeFeedScreen
from .ui.screens.livechat import LiveChatScreen
from .ui.screens.local_playlists import LocalPlaylistsScreen
from .ui.screens.playlist import PlaylistScreen
from .ui.screens.search import SearchScreen
from .ui.screens.settings import SettingsScreen
from .ui.screens.suggestions import SuggestionsScreen
from .ui.widgets.modals import PlaylistPickerModal, TextInputModal
from .ui.widgets.video_list import VideoList

LIVE_POLL_SECONDS = 5 * 60  # feed refresh cadence
# The lives poll is cheap (one in-memory GET on the server) and Twitch lives
# are caught within ~1 min server-side, so clients poll faster than the feed.
LIVE_CHECK_SECONDS = 60
POSITION_HEARTBEAT_SECONDS = 10.0


class YtuiApp(App):
    TITLE = "ytui"

    player_status: reactive[str] = reactive("")

    CSS = """
    #warning-banner {
        width: 100%;
        background: $warning;
        color: $text;
        padding: 0 1;
    }
    #warning-banner.hidden {
        display: none;
    }
    #feed-loading, #search-loading, #channel-loading, #playlist-loading, #detail-loading {
        height: 1;
    }
    Horizontal > VideoList {
        width: 1fr;
    }
    #help-text {
        margin: 2;
        padding: 1 2;
        background: $surface;
        border: round $primary;
        width: auto;
    }
    PlayerBar {
        width: 100%;
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
        display: none;
    }
    PlayerBar.active {
        display: block;
    }
    """

    SCREENS = {
        "home": HomeFeedScreen,
        "search": SearchScreen,
        "help": HelpScreen,
        "history": HistoryScreen,
        "settings": SettingsScreen,
        "local_playlists": LocalPlaylistsScreen,
        "suggestions": SuggestionsScreen,
    }

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.client = YtuiClient(config.server.url, config.server.token)
        self.thumbnails = ThumbnailFetcher()
        self.player = MpvController()
        self.watched: set[str] = set()
        self._last_polled_vid: str | None = None
        self._last_polled_duration: float | None = None
        self._last_position_save = 0.0
        self._live_vids: set[str] = set()
        self._notified_live_ids: set[str] = set()
        self.active_lives: dict[str, Video] = {}
        self._sponsor_segments: dict[str, list[SponsorSegment]] = {}
        self._sponsor_pending: set[str] = set()

    def on_mount(self) -> None:
        self.push_screen("home")
        if not self.config.server.url:
            self.notify(
                "No server configured. Set [server] url and token in config.toml.",
                severity="warning",
                timeout=10,
            )
        self.set_interval(1.0, self._poll_player)
        self.run_worker(self.refresh_watched(), group="watched")
        if self.config.server.url:
            self.set_interval(LIVE_CHECK_SECONDS, self._start_live_check)
            self.set_interval(LIVE_POLL_SECONDS, self._refresh_feed)
            self.set_timer(15, self._start_live_check)

    async def refresh_watched(self) -> None:
        """Fetch the watched-ids set from the server and repaint the ✓ markers."""
        try:
            self.watched = await self.client.watched_ids()
        except YtuiApiError:
            return
        self._refresh_watched_markers()

    # -- live notifications --

    def _start_live_check(self) -> None:
        self.run_worker(self._check_lives(), group="live-check", exclusive=True)

    async def _check_lives(self) -> None:
        try:
            lives = await self.client.lives()
        except YtuiApiError:
            return  # offline / server unreachable: retry at the next interval
        found = {video.video_id: video for video in lives}
        for video in lives:
            if video.video_id not in self._notified_live_ids:
                self._notified_live_ids.add(video.video_id)
                if video.platform == "twitch":
                    self.notify(
                        f"\U0001f7e3 Twitch: {video.channel_title} \u2014 {video.title}",
                        timeout=10,
                    )
                elif video.platform == "tiktok":
                    self.notify(
                        f"\U0001f3b5 TikTok: {video.channel_title} \u2014 {video.title}",
                        timeout=10,
                    )
                else:
                    self.notify(
                        f"\U0001f534 Live: {video.channel_title} \u2014 {video.title}",
                        timeout=10,
                    )
                self.run_worker(
                    lambda v=video: self._notify_live_blocking(v),
                    thread=True,
                    group="live-notify",
                    exclusive=False,
                )
        if found != self.active_lives:
            self.active_lives = found
            if isinstance(self.screen, HomeFeedScreen):
                self.screen.refresh_lives()

    def _notify_live_blocking(self, video: Video) -> None:
        # Blocks until the notification is dismissed or clicked (notify-send --wait).
        action = send_live_notification(video)
        if action == "watch":
            self.call_from_thread(self.play_video, video)
        elif action:
            self.call_from_thread(self._focus_live, video)

    def _focus_live(self, video: Video) -> None:
        """Bring the home feed to the front, focused on the given live video."""
        while not isinstance(self.screen, HomeFeedScreen) and len(self.screen_stack) > 1:
            self.pop_screen()
        if isinstance(self.screen, HomeFeedScreen):
            self.screen.focus_live(video)

    # -- periodic feed refresh --

    def _refresh_feed(self) -> None:
        """Periodically refresh the home feed if the home screen is visible."""
        if isinstance(self.screen, HomeFeedScreen):
            self.screen.load_feed(force_refresh=False, silent=True)

    async def on_unmount(self) -> None:
        await self.thumbnails.close()
        await self.player.close()
        await self.client.close()

    # -- playback --

    async def _poll_player(self) -> None:
        self.player.process_alive()  # reap an exited mpv
        status = await self.player.status()
        if status is None:
            self.player_status = ""
            return
        icon = "⏸ paused" if status.paused else "▶ playing"
        if status.speed != 1.0:
            icon += f" {status.speed:g}x"
        queued = f" ({status.queued} queued)" if status.queued else ""
        self.player_status = f"{icon}{queued}"
        snap = await self.player.playback_snapshot()
        if snap:
            path, title, position, duration = snap
            vid = video_id_from_url(path)
            if vid:
                if vid not in self.watched:
                    # mpv advanced to a queued video on its own: add it to history.
                    self._record_watch(self._video_for_history(vid, title, path))
                if (
                    self.config.player.sponsorblock
                    and vid not in self._sponsor_segments
                    and vid not in self._sponsor_pending
                ):
                    # Safety net for items that never went through _record_watch.
                    self.run_worker(self._fetch_sponsor(vid), group="sponsor")
                if self.config.player.sponsorblock and vid not in self._live_vids:
                    for seg in self._sponsor_segments.get(vid, ()):
                        if seg.start <= position < seg.end - 0.5:
                            if await self.player.seek_absolute(seg.end):
                                self.notify(
                                    "SponsorBlock: skipped "
                                    f"{seg.category} ({int(seg.end - seg.start)}s)",
                                    timeout=3,
                                )
                            break
                if (
                    vid == self._last_polled_vid
                    and self._last_polled_duration is not None
                    and abs(duration - self._last_polled_duration) > 1.0
                ):
                    # Growing duration = live stream (DVR window): resuming a live
                    # with --start stalls mpv, so never track its position.
                    self._live_vids.add(vid)
                    self.run_worker(self._save_position_async(vid, 0.0, None), group="position")
                if vid not in self._live_vids:
                    now = time.monotonic()
                    if now - self._last_position_save >= POSITION_HEARTBEAT_SECONDS:
                        self._last_position_save = now
                        self.run_worker(
                            self._save_position_async(vid, position, duration),
                            group="position",
                        )
                self._last_polled_vid = vid
                self._last_polled_duration = duration

    async def _save_position_async(
        self, video_id: str, position: float, duration: float | None
    ) -> None:
        try:
            await self.client.save_position(video_id, position, duration)
        except YtuiApiError:
            pass  # best-effort heartbeat

    def _video_for_history(self, video_id: str, title: str, path: str = "") -> Video:
        if "bitchute.com/" in path:
            platform = "bitchute"
        elif "odysee.com/" in path:
            platform = "odysee"
        else:
            platform = "youtube"
        return Video(video_id=video_id, title=title or video_id, kind="video", platform=platform)

    def _notify_resume(self, start: float) -> None:
        if start > 0:
            minutes, seconds = divmod(int(start), 60)
            self.notify(f"Resuming at {minutes}:{seconds:02d}", timeout=4)

    async def _resume_start_for(self, video: Video) -> float:
        if video.kind != "video":
            return 0.0
        try:
            row = await self.client.resume(video.video_id)
        except YtuiApiError:
            return 0.0
        if row is None:
            return 0.0
        return resume_start(row[0], row[1])

    def play_video(self, video: Video, audio_only: bool | None = None) -> None:
        self._record_watch(video)
        self.run_worker(self._play_video_async(video, audio_only), group="player")

    async def _playback_url(self, video: Video) -> str:
        """Resolved stream URL for a Twitch/TikTok live, else the plain URL.

        Live ids carry "login:stream_id"; VODs and other platforms play
        directly. Falls back to the page URL when resolution fails (Twitch:
        direct ad-fed stream; TikTok: /live page via yt-dlp).
        """
        if video.platform not in ("twitch", "tiktok") or ":" not in video.video_id:
            return video.url
        try:
            streams = await self.client.video_streams(video.video_id, platform=video.platform)
        except YtuiApiError:
            return video.url
        return streams.get("url") or video.url

    async def _play_video_async(self, video: Video, audio_only: bool | None) -> None:
        start = await self._resume_start_for(video)
        url = await self._playback_url(video)
        if audio_only:
            # One-shot audio playback, separate from the controlled queue.
            try:
                play(url, self.config.player, audio_only=True, start=start or None)
                self._notify_resume(start)
                self.notify("Playing audio in mpv…", timeout=4)
            except PlayerError as exc:
                self.notify(str(exc), severity="error", timeout=10)
            return
        self._notify_resume(start)
        await self._play_async(url, start=start or None)
        if video.platform in ("youtube", "twitch") and (
            video.video_id in self.active_lives or ":" in video.video_id
        ):
            self.push_screen(LiveChatScreen(video))

    def play_from_history(self, video: Video) -> None:
        """Replay from the history screen: resume position and playlist context."""
        self._record_watch(video)
        self.run_worker(self._play_from_history_async(video), group="player")

    async def _play_from_history_async(self, video: Video) -> None:
        row = None
        if video.kind == "video":
            try:
                row = await self.client.resume(video.video_id)
            except YtuiApiError:
                row = None
        if row is None or not row[2]:
            await self._play_video_async(video, audio_only=None)
            return
        playlist_id = row[2]
        start = resume_start(row[0], row[1])
        self._notify_resume(start)
        try:
            entries = await self.client.playlist_videos(playlist_id, platform=video.platform)
        except YtuiApiError:
            self.notify("Playlist unavailable, playing video only.", timeout=5)
            await self._play_resumed_async(await self._playback_url(video), start, [])
            return
        idx = next(
            (i for i, e in enumerate(entries) if e.video_id == video.video_id),
            None,
        )
        if idx is None:
            self.notify("Video no longer in playlist, playing video only.", timeout=5)
            await self._play_resumed_async(await self._playback_url(video), start, [])
            return
        rest = [e.url for e in entries[idx + 1 :]]
        await self._play_resumed_async(await self._playback_url(video), start, rest)

    async def _play_resumed_async(self, url: str, start: float, queue: list[str]) -> None:
        try:
            await self.player.play(url, self.config.player, start=start or None)
            for u in queue:
                await self.player.enqueue(u, self.config.player)
        except PlayerError as exc:
            self.notify(str(exc), severity="error", timeout=10)
            return
        queued = f" ({len(queue)} queued)" if queue else ""
        self.notify(f"Playing in mpv…{queued}", timeout=4)

    def enqueue_video(self, video: Video) -> None:
        if video.kind == "channel":
            self.notify("Channels cannot be enqueued.", severity="warning", timeout=5)
            return
        self._record_watch(video)
        self.run_worker(self._enqueue_async(video), group="player")

    def play_all(self, videos: list[Video]) -> None:
        """Play the first item and append the rest to the mpv queue, in order."""
        playable = [v for v in videos if v.kind != "channel"]
        if not playable:
            self.notify("Nothing to play.", timeout=4)
            return
        for video in playable:
            self._record_watch(video)
        self.run_worker(self._play_all_async(playable), group="player")

    async def _play_all_async(self, videos: list[Video]) -> None:
        urls = [await self._playback_url(v) for v in videos]
        try:
            await self.player.play(urls[0], self.config.player)
            for url in urls[1:]:
                await self.player.enqueue(url, self.config.player)
        except PlayerError as exc:
            self.notify(str(exc), severity="error", timeout=10)
            return
        queued = f" ({len(urls) - 1} queued)" if len(urls) > 1 else ""
        self.notify(f"Playing in mpv…{queued}", timeout=4)

    async def _play_async(self, url: str, start: float | None = None) -> None:
        try:
            await self.player.play(url, self.config.player, start=start)
            self.notify("Playing in mpv…", timeout=4)
        except PlayerError as exc:
            self.notify(str(exc), severity="error", timeout=10)

    async def _enqueue_async(self, video: Video) -> None:
        url = await self._playback_url(video)
        try:
            appended = await self.player.enqueue(url, self.config.player)
        except PlayerError as exc:
            self.notify(str(exc), severity="error", timeout=10)
            return
        self.notify("Added to mpv queue." if appended else "Playing in mpv…", timeout=4)

    async def pause_toggle(self) -> None:
        if not await self.player.pause_toggle():
            self.notify("Nothing is playing.", timeout=3)

    async def playlist_next(self) -> None:
        if not await self.player.playlist_next():
            self.notify("Nothing is playing.", timeout=3)

    async def change_speed(self, delta: float) -> None:
        current = await self.player.speed()
        if current is None:
            self.notify("Nothing is playing.", timeout=3)
            return
        new = min(3.0, max(0.25, round(current + delta, 2)))
        if await self.player.set_speed(new):
            self.notify(f"Speed: {new:g}x", timeout=2)

    async def cycle_subtitles(self) -> None:
        label = await self.player.cycle_subtitles()
        if label is None:
            self.notify("Nothing is playing.", timeout=3)
            return
        self.notify(f"Subtitles: {label}", timeout=3)

    def _record_watch(self, video: Video) -> None:
        if video.kind == "channel":
            return
        self.watched.add(video.video_id)
        self._refresh_watched_markers()
        self.run_worker(self._record_watch_async(video), group="history")
        if (
            self.config.player.sponsorblock
            and video.kind == "video"
            and video.platform == "youtube"
        ):
            self.run_worker(self._fetch_sponsor(video.video_id), group="sponsor")

    async def _fetch_sponsor(self, video_id: str) -> None:
        if video_id in self._sponsor_segments or video_id in self._sponsor_pending:
            return
        self._sponsor_pending.add(video_id)
        try:
            segments = await self.client.sponsor_segments(video_id)
        except YtuiApiError:
            segments = []  # don't retry this session
        finally:
            self._sponsor_pending.discard(video_id)
        if len(self._sponsor_segments) > 100:
            self._sponsor_segments.clear()
        self._sponsor_segments[video_id] = segments

    async def _record_watch_async(self, video: Video) -> None:
        try:
            await self.client.record_watch(video)
        except YtuiApiError:
            pass  # best-effort: history lives on the server

    def _refresh_watched_markers(self) -> None:
        for screen in self.screen_stack:
            for video_list in screen.query(VideoList):
                video_list.refresh_watched(self.watched)

    # -- navigation / item actions --

    def open_channel(self, video: Video) -> None:
        """Open the channel of the given item (or the item itself if it is a channel)."""
        if video.kind != "channel" and not video.channel_id:
            self.notify("No channel information for this item.", severity="warning", timeout=5)
            return
        self.push_screen(ChannelScreen(video))

    def open_playlist(self, video: Video) -> None:
        self.push_screen(PlaylistScreen(video))

    def open_detail(self, video: Video) -> None:
        if video.kind != "video":
            self.notify("Details are only available for videos.", timeout=5)
            return
        self.push_screen(VideoDetailScreen(video))

    def follow_channel(self, video: Video) -> None:
        """Follow the item's channel on the server."""
        channel_id = video.video_id if video.kind == "channel" else video.channel_id
        if channel_id and video.platform == "bitchute":
            channel_id = f"bitchute:{channel_id}"
        elif channel_id and video.platform == "odysee":
            channel_id = f"odysee:{channel_id}"
        elif channel_id and video.platform == "twitch":
            channel_id = f"twitch:{channel_id}"
        elif channel_id and video.platform == "tiktok":
            channel_id = f"tiktok:{channel_id}"
        if not channel_id:
            self.notify("No channel ID for this item.", severity="warning", timeout=5)
            return
        name = video.channel_title or video.title or channel_id
        self.run_worker(self._follow_channel_async(channel_id, name), group="channels")

    async def _follow_channel_async(self, ref: str, name: str) -> None:
        try:
            await self.client.follow_channel(ref)
        except YtuiApiError as exc:
            if exc.status_code == 409:
                self.notify(f"{name} is already in your channels.", timeout=5)
            else:
                self.notify(f"Could not follow {name}: {exc.detail}", severity="error", timeout=8)
            return
        self.notify(f"Added {name} to your channels.", timeout=5)

    def download_video(self, video: Video) -> None:
        if video.kind == "channel":
            self.notify("Channels cannot be downloaded.", severity="warning", timeout=5)
            return
        self.notify(f"Downloading: {video.title}", timeout=5)
        self.run_worker(
            lambda: self._download_blocking(video), thread=True, group="download", exclusive=False
        )

    def _download_blocking(self, video: Video) -> None:
        from .player.download import download_video

        try:
            target = download_video(video.url, self.config.player)
        except Exception as exc:
            self.call_from_thread(
                self.notify, f"Download failed: {exc}", severity="error", timeout=10
            )
            return
        self.call_from_thread(self.notify, f"Downloaded to {target}: {video.title}", timeout=8)

    def _youtube_video_or_notify(self, video: Video) -> bool:
        if video.kind != "video":
            self.notify("Only videos can be liked or commented.", severity="warning", timeout=5)
            return False
        if video.platform == "odysee":
            self.notify(
                "Odysee: likes/comments are read-only (open details to view comments).",
                severity="warning",
                timeout=5,
            )
            return False
        if video.platform != "youtube":
            self.notify("Only YouTube videos support this action.", severity="warning", timeout=5)
            return False
        return True

    def like_video_action(self, video: Video) -> None:
        if not self._youtube_video_or_notify(video):
            return
        self.notify(f"Liking: {video.title}", timeout=4)
        self.run_worker(self._like_async(video), group="youtube-auth")

    async def _like_async(self, video: Video) -> None:
        try:
            await self.client.like_video(video.video_id)
        except YtuiApiError as exc:
            detail = exc.detail
            if exc.status_code == 409:
                detail = f"{detail} — run 'ytui auth push' from this machine first."
            self.notify(detail, severity="error", timeout=10)
            return
        self.notify(f"Liked: {video.title}", timeout=5)

    def comment_video_action(self, video: Video) -> None:
        if not self._youtube_video_or_notify(video):
            return

        def on_text(text: str | None) -> None:
            if not text:
                return
            self.run_worker(self._comment_async(video, text), group="youtube-auth")

        self.push_screen(TextInputModal(f"Comment on: {video.title}"), on_text)

    async def _comment_async(self, video: Video, text: str) -> None:
        try:
            await self.client.comment_video(video.video_id, text)
        except YtuiApiError as exc:
            detail = exc.detail
            if exc.status_code == 409:
                detail = f"{detail} — run 'ytui auth push' from this machine first."
            self.notify(detail, severity="error", timeout=10)
            return
        self.notify(f"Comment posted on: {video.title}", timeout=5)

    def save_to_local_playlist(self, video: Video) -> None:
        if video.kind == "channel":
            self.notify("Channels cannot be added to a playlist.", severity="warning", timeout=5)
            return

        def on_picked(playlist_id: int | None) -> None:
            if playlist_id is None:
                return
            self.run_worker(self._save_to_playlist_async(playlist_id, video), group="playlists")

        self.push_screen(PlaylistPickerModal(), on_picked)

    async def _save_to_playlist_async(self, playlist_id: int, video: Video) -> None:
        try:
            added = await self.client.add_playlist_item(playlist_id, video)
        except YtuiApiError as exc:
            self.notify(f"Could not save: {exc.detail}", severity="error", timeout=8)
            return
        if added:
            self.notify(f"Saved: {video.title}", timeout=4)
        else:
            self.notify("Already in that playlist.", timeout=4)
