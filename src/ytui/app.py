"""Textual application: screens, API client, playback orchestration."""

from __future__ import annotations

import time
from collections import OrderedDict, deque

from textual.app import App
from textual.reactive import reactive

from .api_client import YtuiApiError, YtuiClient, resume_start
from .config import Config, set_option
from .livenotify import send_live_notification
from .models import SponsorSegment, Video, video_id_from_url
from .player.mpv import Media, MpvController, PlayerError, Subtitle, play
from .thumbnails.fetcher import ThumbnailFetcher
from .ui.screens.channel import ChannelScreen
from .ui.screens.detail import VideoDetailScreen
from .ui.screens.help import HelpScreen
from .ui.screens.history import HistoryScreen
from .ui.screens.home import HomeFeedScreen
from .ui.screens.livechat import LiveChatScreen
from .ui.screens.local_playlists import LocalPlaylistsScreen
from .ui.screens.music import MusicScreen
from .ui.screens.playlist import PlaylistScreen
from .ui.screens.search import SearchScreen
from .ui.screens.settings import SettingsScreen
from .ui.screens.suggestions import SuggestionsScreen
from .ui.widgets.modals import PlaylistPickerModal, QualityPickerModal, TextInputModal
from .ui.widgets.video_list import VideoList

LIVE_POLL_SECONDS = 5 * 60  # feed refresh cadence
# The lives poll is cheap (one in-memory GET on the server) and Twitch lives
# are caught within ~1 min server-side, so clients poll faster than the feed.
LIVE_CHECK_SECONDS = 60
POSITION_HEARTBEAT_SECONDS = 10.0
# Server-resolved queue entries kept ahead of the playing one. The rest is
# resolved as mpv advances: a 50-video playlist must not fire 50 extractions
# (through the account session) before the first one even plays.
QUEUE_LOOKAHEAD = 2
_RESOLVED_MEMORY = 256  # resolved URL -> Media, for identity in the poller


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
    #feed-loading, #search-loading, #channel-loading, #playlist-loading, #detail-loading,
    #music-loading {
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
        "music": MusicScreen,
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
        self._playing: Video | None = None
        # mpv opens server-resolved stream URLs: map them back to their video.
        self._resolved: OrderedDict[str, Media] = OrderedDict()
        self._subs_loaded_for: int | None = None  # mpv playlist entry id
        # Videos still to append to mpv's queue (lazily resolved, in order).
        self._pending: deque[Video] = deque()
        self._topping_up = False

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
        alive = self.player.process_alive()  # reap an exited mpv
        status = await self.player.status()
        if status is None:
            self.player_status = ""
            if not alive:
                self._pending.clear()  # player closed: the rest of the queue goes too
            return
        icon = "⏸ paused" if status.paused else "▶ playing"
        if status.speed != 1.0:
            icon += f" {status.speed:g}x"
        pending = status.queued + len(self._pending)
        queued = f" ({pending} queued)" if pending else ""
        self.player_status = f"{icon}{queued}"
        if self._pending and status.queued < QUEUE_LOOKAHEAD and not self._topping_up:
            self._topping_up = True
            self.run_worker(self._top_up_queue(), group="queue-topup")
        snap = await self.player.playback_snapshot()
        if snap:
            path, title, position, duration = snap
            media = self._resolved.get(path)
            if media is not None and media.subtitles:
                entry = await self.player.current_entry_id()
                if entry is not None and entry != self._subs_loaded_for:
                    self._subs_loaded_for = entry
                    await self.player.add_subtitles(media)
            ref = (media.video_id, media.platform) if media else video_id_from_url(path)
            if ref:
                vid, platform = ref
                qualified = f"{platform}:{vid}"
                if qualified not in self.watched:
                    # mpv advanced to a queued video on its own: add it to history.
                    self._record_watch(self._video_for_history(vid, title, platform))
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
                    self.run_worker(
                        self._save_position_async(vid, 0.0, None, platform), group="position"
                    )
                if vid not in self._live_vids:
                    now = time.monotonic()
                    if now - self._last_position_save >= POSITION_HEARTBEAT_SECONDS:
                        self._last_position_save = now
                        self.run_worker(
                            self._save_position_async(vid, position, duration, platform),
                            group="position",
                        )
                self._last_polled_vid = vid
                self._last_polled_duration = duration

    async def _save_position_async(
        self,
        video_id: str,
        position: float,
        duration: float | None,
        platform: str = "youtube",
    ) -> None:
        try:
            await self.client.save_position(video_id, position, duration, platform=platform)
        except YtuiApiError:
            pass  # best-effort heartbeat

    def _video_for_history(self, video_id: str, title: str, platform: str = "youtube") -> Video:
        return Video(video_id=video_id, title=title or video_id, kind="video", platform=platform)

    def _notify_resume(self, start: float) -> None:
        if start > 0:
            minutes, seconds = divmod(int(start), 60)
            self.notify(f"Resuming at {minutes}:{seconds:02d}", timeout=4)

    async def _resume_start_for(self, video: Video) -> float:
        if video.kind != "video":
            return 0.0
        try:
            row = await self.client.resume(video.video_id, video.platform)
        except YtuiApiError:
            return 0.0
        if row is None:
            return 0.0
        return resume_start(row[0], row[1])

    def play_video(self, video: Video, audio_only: bool | None = None) -> None:
        self._record_watch(video)
        self.run_worker(self._play_video_async(video, audio_only), group="player")

    def _remember(self, media: Media) -> Media:
        self._resolved[media.url] = media
        self._resolved.move_to_end(media.url)
        while len(self._resolved) > _RESOLVED_MEMORY:
            self._resolved.popitem(last=False)
        return media

    async def _playback_url(
        self, video: Video, *, audio_only: bool = False, max_height: int | None = None
    ) -> str | Media:
        """What mpv should open for `video`.

        YouTube VODs are resolved by the server (Media): it extracts with the
        pushed account session, drops dead URLs and keeps the original audio
        language — mpv's local yt-dlp has none of that (measured 2026-10-16:
        it picked an audio URL answering 403 and mpv closed at once). Twitch/
        TikTok live ids get their resolved stream URL. Everything else, YouTube
        lives included, and any server failure, keeps the page URL for mpv's
        own yt-dlp.
        """
        height = max_height or self.config.player.max_height
        if video.platform == "youtube" and self._server_resolves(video):
            try:
                streams = await self.client.video_streams(
                    video.video_id,
                    platform="youtube",
                    max_height=height,
                    audio_only=audio_only,
                    sub_langs=None if audio_only else self.config.player.subtitle_langs,
                )
            except YtuiApiError as exc:
                self.notify(
                    f"Server could not resolve the stream ({exc}); trying local yt-dlp.",
                    severity="warning",
                    timeout=6,
                )
                return video.url
            media = _media_from_streams(video, streams)
            return self._remember(media) if media is not None else video.url
        if video.platform not in ("twitch", "tiktok") or ":" not in video.video_id:
            return video.url
        try:
            streams = await self.client.video_streams(
                video.video_id,
                platform=video.platform,
                max_height=height,
            )
        except YtuiApiError:
            return video.url
        return streams.get("url") or video.url

    def _server_resolves(self, video: Video) -> bool:
        """YouTube VODs with a server to ask (lives keep mpv's yt-dlp)."""
        return (
            bool(self.config.server.url)
            and video.kind == "video"
            and video.video_id not in self.active_lives
            and video.video_id not in self._live_vids
        )

    async def _top_up_queue(self) -> None:
        """Resolve and append pending videos until QUEUE_LOOKAHEAD are queued."""
        try:
            while self._pending:
                status = await self.player.status()
                if status is None or status.queued >= QUEUE_LOOKAHEAD:
                    return
                video = self._pending.popleft()
                item = await self._playback_url(video)
                try:
                    await self.player.enqueue(item, self.config.player)
                except PlayerError:
                    self._pending.clear()
                    return
        finally:
            self._topping_up = False

    async def _play_video_async(self, video: Video, audio_only: bool | None) -> None:
        start = await self._resume_start_for(video)
        self._playing = video
        self._pending.clear()
        url = await self._playback_url(video, audio_only=bool(audio_only))
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

    def play_also(self, video: Video) -> None:
        """Play in a separate one-shot mpv; the controlled player keeps running."""
        if video.kind == "channel":
            self.notify("Channels cannot be played (press o to open).", timeout=5)
            return
        self._record_watch(video)
        # exclusive=False so a second Ctrl+Enter does not cancel the first
        # (Twitch/TikTok URL resolution can take a few seconds).
        self.run_worker(
            self._play_also_async(video), group="player-extra", exclusive=False
        )

    async def _play_also_async(self, video: Video) -> None:
        start = await self._resume_start_for(video)
        url = await self._playback_url(video)
        try:
            play(url, self.config.player, start=start or None)
        except PlayerError as exc:
            self.notify(str(exc), severity="error", timeout=10)
            return
        self._notify_resume(start)
        self.notify(f"Also playing: {video.title}", timeout=4)

    def play_from_history(self, video: Video) -> None:
        """Replay from the history screen: resume position and playlist context."""
        self._record_watch(video)
        self.run_worker(self._play_from_history_async(video), group="player")

    async def _play_from_history_async(self, video: Video) -> None:
        self._playing = video
        row = None
        if video.kind == "video":
            try:
                row = await self.client.resume(video.video_id, video.platform)
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
        rest = [e for e in entries[idx + 1 :] if e.kind != "channel"]
        await self._play_resumed_async(await self._playback_url(video), start, rest)

    async def _play_resumed_async(
        self, url: str | Media, start: float, queue: list[Video]
    ) -> None:
        self._pending.clear()
        try:
            await self.player.play(url, self.config.player, start=start or None)
        except PlayerError as exc:
            self.notify(str(exc), severity="error", timeout=10)
            return
        self._pending.extend(queue)
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

    def play_listed(self, videos: list[Video]) -> None:
        """Play a listed playlist as an mpv queue, recording only the first item.

        Unlike play_all (explicit picks), the rest is merely queued, so it must
        not all land in the watch history up front.
        """
        playable = [v for v in videos if v.kind != "channel"]
        if not playable:
            self.notify("Nothing to play.", timeout=4)
            return
        self._record_watch(playable[0])
        self.run_worker(self._play_all_async(playable), group="player")

    async def _play_all_async(self, videos: list[Video]) -> None:
        self._playing = videos[0]
        self._pending.clear()
        try:
            await self.player.play(await self._playback_url(videos[0]), self.config.player)
        except PlayerError as exc:
            self.notify(str(exc), severity="error", timeout=10)
            return
        # The rest is resolved lazily by the poller (QUEUE_LOOKAHEAD ahead).
        self._pending.extend(videos[1:])
        queued = f" ({len(videos) - 1} queued)" if len(videos) > 1 else ""
        self.notify(f"Playing in mpv…{queued}", timeout=4)

    async def _play_async(self, url: str | Media, start: float | None = None) -> None:
        try:
            await self.player.play(url, self.config.player, start=start)
            self.notify("Playing in mpv…", timeout=4)
        except PlayerError as exc:
            self.notify(str(exc), severity="error", timeout=10)

    async def _enqueue_async(self, video: Video) -> None:
        if self._pending:
            # Keep the order: it lands after the not-yet-resolved entries.
            self._pending.append(video)
            self.notify("Added to mpv queue.", timeout=4)
            return
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

    async def change_quality(self, max_height: int) -> None:
        """Persist the height cap and re-open the current file at its position."""
        self.config.player.max_height = max_height
        set_option("player", "max_height", max_height)
        video = self._playing
        snapshot = await self.player.playback_snapshot()
        if video is None or snapshot is None:
            self.notify(f"Max height: {max_height}p", timeout=3)
            return
        _path, _title, position, _duration = snapshot
        # Never resume a live at a position: mpv stalls on a DVR window (see
        # _poll_player). A Twitch/TikTok live id also needs a re-resolved URL,
        # which _playback_url does (it returns video.url for everything else).
        live = (
            video.video_id in self._live_vids
            or video.video_id in self.active_lives
            or (video.platform in ("twitch", "tiktok") and ":" in video.video_id)
        )
        url = await self._playback_url(video, max_height=max_height)
        start = None if live else position
        if isinstance(url, Media):
            # Server-resolved: the new cap is already in the resolved URL.
            await self.player.play(url, self.config.player, start=start)
            self.notify(f"Max height: {max_height}p", timeout=3)
        elif await self.player.reload_with_quality(url, max_height, start):
            self.notify(f"Max height: {max_height}p", timeout=3)
        else:
            self.notify("Could not switch quality.", severity="warning", timeout=5)

    def pick_quality(self) -> None:
        def on_pick(height: int | None) -> None:
            if height is not None:
                self.run_worker(self.change_quality(height), group="player")

        self.push_screen(QualityPickerModal(self.config.player.max_height), on_pick)

    async def cycle_subtitles(self) -> None:
        label = await self.player.cycle_subtitles()
        if label is None:
            self.notify("Nothing is playing.", timeout=3)
            return
        self.notify(f"Subtitles: {label}", timeout=3)

    def _record_watch(self, video: Video) -> None:
        if video.kind == "channel":
            return
        self.watched.add(f"{video.platform}:{video.video_id}")
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
        elif channel_id and video.platform == "crowdbunker":
            channel_id = f"crowdbunker:{channel_id}"
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

    def share_link_action(self, video: Video) -> None:
        """Copy the private ytui link of a video (playable without the token)."""
        self.run_worker(self._share_link_async(video), group="share-link")

    async def _share_link_async(self, video: Video) -> None:
        try:
            url, expires_at = await self.client.create_share(video)
        except YtuiApiError as exc:
            detail = "This live has ended." if exc.status_code == 409 else exc.detail
            self.notify(f"ytui link failed: {detail}", severity="error", timeout=8)
            return
        self.copy_to_clipboard(url)  # OSC 52, like "y"
        until = f" (valid until {expires_at[:10]})" if expires_at else ""
        self.notify(f"ytui link copied{until}: {url}", timeout=8)

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

    def import_playlist_to_local(
        self, source: str, platform: str = "youtube", title: str = ""
    ) -> None:
        """Copy a whole upstream playlist into a local one (picked or created)."""
        if not source.strip():
            return

        def on_picked(playlist_id: int | None) -> None:
            if playlist_id is None:
                return
            self.run_worker(
                self._import_playlist_async(source, platform, playlist_id), group="playlists"
            )

        self.push_screen(
            PlaylistPickerModal(prompt="Import playlist into", default_name=title), on_picked
        )

    async def _import_playlist_async(
        self, source: str, platform: str, playlist_id: int
    ) -> None:
        self.notify("Importing playlist…", timeout=4)
        try:
            result = await self.client.import_playlist(
                source, platform=platform, target_id=playlist_id
            )
        except YtuiApiError as exc:
            self.notify(f"Import failed: {exc.detail}", severity="error", timeout=8)
            return
        skipped = f", {result.skipped} skipped" if result.skipped else ""
        self.notify(
            f"Imported {result.added} videos into {result.playlist.name!r}{skipped}.", timeout=6
        )

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


def _media_from_streams(video: Video, streams: dict) -> Media | None:
    """Media for a /streams answer, or None to leave it to mpv's yt-dlp."""
    if streams.get("is_live"):
        return None  # lives keep mpv's own yt-dlp (DVR window, live edge)
    kind = streams.get("kind")
    title = streams.get("title") or video.title or ""
    subtitles = tuple(
        Subtitle(url=s["url"], lang=s.get("lang") or "", label=s.get("label") or s.get("lang") or "")
        for s in streams.get("subtitles") or []
        if s.get("url")
    )
    if kind == "split" and streams.get("video_url") and streams.get("audio_url"):
        return Media(
            url=streams["video_url"],
            audio_url=streams["audio_url"],
            video_id=video.video_id,
            title=title,
            subtitles=subtitles,
        )
    if kind in ("hls", "progressive") and streams.get("url"):
        return Media(url=streams["url"], video_id=video.video_id, title=title, subtitles=subtitles)
    return None
