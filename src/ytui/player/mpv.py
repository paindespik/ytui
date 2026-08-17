"""mpv playback: detached one-shot launcher and JSON IPC controller (queue, pause, next)."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile

from ..config import PlayerConfig


class PlayerError(Exception):
    pass


def default_socket_path() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(base, "ytui-mpv.sock")


def ytdl_format(max_height: int) -> str:
    """yt-dlp format selector for a height cap ('?' = tolerate no exact match)."""
    return f"bestvideo[height<=?{max_height}]+bestaudio/best"


def build_command(
    url: str,
    player: PlayerConfig,
    audio_only: bool | None = None,
    ipc_socket: str | None = None,
    start: float | None = None,
) -> list[str]:
    """Build the mpv command line. Raises PlayerError if mpv is missing."""
    if shutil.which(player.command) is None:
        raise PlayerError(
            f"Player '{player.command}' not found. Install mpv (e.g. 'sudo apt install mpv') "
            "or set [player].command in the config."
        )
    cmd = [player.command, "--no-terminal", "--force-window=immediate"]
    audio = player.audio_only if audio_only is None else audio_only
    if audio:
        cmd.append("--no-video")
    else:
        cmd.append(f"--ytdl-format={ytdl_format(player.max_height)}")
        if player.subtitle_langs:
            # Expose YouTube subtitle tracks (manual + auto captions) through
            # mpv's ytdl hook; they are not selected by default. Options without
            # an argument need the trailing '='.
            cmd.append(f"--ytdl-raw-options-append=sub-langs={player.subtitle_langs}")
            cmd.append("--ytdl-raw-options-append=write-subs=")
            cmd.append("--ytdl-raw-options-append=write-auto-subs=")
    if ipc_socket:
        cmd.append(f"--input-ipc-server={ipc_socket}")
    if start and start > 0:
        # Per-file option: a global --start would also apply to any file
        # loaded later over IPC (queue, live streams), stalling playback.
        cmd.extend(["--{", f"--start={start:.0f}", url, "--}"])
    else:
        cmd.append(url)
    return cmd


def _popen(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def play(
    url: str,
    player: PlayerConfig,
    audio_only: bool | None = None,
    start: float | None = None,
) -> None:
    """Start a detached one-shot mpv (used by the CLI and per-video audio-only)."""
    _popen(build_command(url, player, audio_only, start=start))


class PlayerStatus:
    """Snapshot of the controlled mpv instance."""

    def __init__(self, paused: bool, queued: int, speed: float = 1.0) -> None:
        self.paused = paused
        self.queued = queued
        self.speed = speed


class MpvController:
    """Controls a single mpv instance over its JSON IPC socket.

    If an instance is alive, enqueue() appends to its playlist; otherwise a new
    process is spawned with --input-ipc-server.
    """

    def __init__(self, socket_path: str | None = None) -> None:
        self.socket_path = socket_path or default_socket_path()
        self._proc: subprocess.Popen | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request_id = 0
        self._lock = asyncio.Lock()

    def process_alive(self) -> bool:
        if self._proc is not None and self._proc.poll() is not None:
            self._proc = None  # reap exited process
        return self._proc is not None

    def _spawn(
        self,
        url: str,
        player: PlayerConfig,
        audio_only: bool | None = None,
        start: float | None = None,
    ) -> None:
        try:
            os.unlink(self.socket_path)  # remove stale socket file
        except OSError:
            pass
        self._proc = _popen(
            build_command(url, player, audio_only, ipc_socket=self.socket_path, start=start)
        )

    async def _disconnect(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def _connect(self, retries: int = 1) -> bool:
        if self._writer is not None:
            return True
        for attempt in range(retries):
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(self.socket_path)
                return True
            except OSError:
                if attempt + 1 < retries:
                    await asyncio.sleep(0.2)
        return False

    async def command(self, *args: object, connect_retries: int = 1) -> dict | None:
        """Send one IPC command; return mpv's response dict, or None on failure."""
        async with self._lock:
            if not await self._connect(retries=connect_retries):
                return None
            assert self._reader is not None and self._writer is not None
            self._request_id += 1
            req_id = self._request_id
            payload = json.dumps({"command": list(args), "request_id": req_id}) + "\n"
            try:
                self._writer.write(payload.encode())
                await self._writer.drain()
                while True:
                    line = await asyncio.wait_for(self._reader.readline(), timeout=2.0)
                    if not line:
                        raise ConnectionError("socket closed")
                    msg = json.loads(line)
                    if msg.get("request_id") == req_id:
                        return msg
            except (OSError, ConnectionError, asyncio.TimeoutError, json.JSONDecodeError):
                await self._disconnect()
                return None

    async def is_alive(self) -> bool:
        """True if the spawned process runs or something answers on the socket."""
        if self.process_alive():
            return True
        await self._disconnect()
        return await self._connect(retries=1)

    async def play(self, url: str, player: PlayerConfig, start: float | None = None) -> None:
        """Play now: replace playback in the live instance, or spawn a new one."""
        if await self.is_alive():
            resp = await self._loadfile_replace(url, start)
            if resp is not None and resp.get("error") == "success":
                return
            await self._disconnect()
        self._spawn(url, player, start=start)

    async def _loadfile_replace(self, url: str, start: float | None) -> dict | None:
        if not start or start <= 0:
            return await self.command("loadfile", url, "replace", connect_retries=25)
        opts = f"start={start:.0f}"
        # mpv >= 0.38 inserted an index argument before the options string.
        resp = await self.command("loadfile", url, "replace", -1, opts, connect_retries=25)
        if resp is not None and resp.get("error") == "success":
            return resp
        resp = await self.command("loadfile", url, "replace", opts)
        if resp is not None and resp.get("error") == "success":
            return resp
        # Playing without resume beats not playing at all.
        return await self.command("loadfile", url, "replace")

    async def reload_with_quality(
        self, url: str, max_height: int, start: float | None
    ) -> bool:
        """Reload `url` with a new height cap, resuming at `start`.

        --ytdl-format only applies when mpv is spawned, so a live quality change
        goes through per-file loadfile options (verified on mpv 0.41).
        """
        opts = f"ytdl-format={ytdl_format(max_height)}"
        if start and start > 0:
            opts = f"start={start:.0f}," + opts
        resp = await self.command("loadfile", url, "replace", -1, opts, connect_retries=25)
        if resp is not None and resp.get("error") == "success":
            return True
        # mpv < 0.38: no index argument.
        resp = await self.command("loadfile", url, "replace", opts)
        return resp is not None and resp.get("error") == "success"

    async def enqueue(self, url: str, player: PlayerConfig) -> bool:
        """Append to the live queue (True) or start a new player (False)."""
        if await self.is_alive():
            resp = await self.command("loadfile", url, "append-play", connect_retries=25)
            if resp is not None and resp.get("error") == "success":
                return True
            await self._disconnect()
        self._spawn(url, player)
        return False

    async def pause_toggle(self) -> bool:
        resp = await self.command("cycle", "pause")
        return resp is not None and resp.get("error") == "success"

    async def playlist_next(self) -> bool:
        resp = await self.command("playlist-next")
        return resp is not None and resp.get("error") == "success"

    async def seek_absolute(self, seconds: float) -> bool:
        resp = await self.command("seek", seconds, "absolute+exact")
        return resp is not None and resp.get("error") == "success"

    async def set_speed(self, speed: float) -> bool:
        resp = await self.command("set_property", "speed", round(speed, 2))
        return resp is not None and resp.get("error") == "success"

    async def speed(self) -> float | None:
        resp = await self.command("get_property", "speed")
        if resp is None or resp.get("error") != "success":
            return None
        return float(resp.get("data") or 1.0)

    async def cycle_subtitles(self) -> str | None:
        """Cycle sub tracks; return the new track label, 'off', or None if no player."""
        resp = await self.command("cycle", "sub")
        if resp is None or resp.get("error") != "success":
            return None
        cur = await self.command("get_property", "current-tracks/sub")
        if cur is None or cur.get("error") != "success" or not cur.get("data"):
            return "off"
        data = cur["data"]
        return data.get("title") or data.get("lang") or "on"

    async def playback_snapshot(self) -> tuple[str, str, float, float] | None:
        """(path, title, time_pos, duration) of the current file, or None if unavailable."""
        values = []
        for prop in ("path", "time-pos", "duration"):
            resp = await self.command("get_property", prop)
            if resp is None or resp.get("error") != "success" or resp.get("data") is None:
                return None
            values.append(resp["data"])
        path, time_pos, duration = values
        if not isinstance(duration, (int, float)) or duration <= 0:
            return None  # live streams have no usable duration
        title_resp = await self.command("get_property", "media-title")
        title = ""
        if title_resp is not None and title_resp.get("error") == "success":
            title = str(title_resp.get("data") or "")
        return str(path), title, float(time_pos), float(duration)

    async def status(self) -> PlayerStatus | None:
        """Current playback status, or None when nothing is playing."""
        resp = await self.command("get_property", "playlist-count")
        if resp is None or resp.get("error") != "success":
            return None
        count = resp.get("data") or 0
        pos_resp = await self.command("get_property", "playlist-pos")
        pos = pos_resp.get("data") if pos_resp and pos_resp.get("error") == "success" else -1
        pause_resp = await self.command("get_property", "pause")
        paused = bool(pause_resp.get("data")) if pause_resp else False
        speed_resp = await self.command("get_property", "speed")
        speed = 1.0
        if speed_resp is not None and speed_resp.get("error") == "success":
            speed = float(speed_resp.get("data") or 1.0)
        queued = max(0, count - pos - 1) if isinstance(pos, int) and pos >= 0 else 0
        return PlayerStatus(paused=paused, queued=queued, speed=speed)

    async def close(self) -> None:
        await self._disconnect()
