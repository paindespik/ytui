"""MpvController IPC command formatting against a fake mpv socket (no real mpv)."""

import asyncio
import json

import pytest

from ytui.config import PlayerConfig
from ytui.player.mpv import MpvController, PlayerError, build_command


class FakeMpv:
    """Minimal JSON IPC server that records commands and answers success."""

    def __init__(self, socket_path):
        self.socket_path = str(socket_path)
        self.commands: list[list] = []
        self.properties: dict[str, object] = {
            "playlist-count": 3,
            "playlist-pos": 0,
            "pause": False,
        }
        self._server = None

    async def start(self):
        self._server = await asyncio.start_unix_server(self._handle, path=self.socket_path)

    async def _handle(self, reader, writer):
        while True:
            line = await reader.readline()
            if not line:
                break
            msg = json.loads(line)
            self.commands.append(msg["command"])
            data = None
            if msg["command"][0] == "get_property":
                data = self.properties.get(msg["command"][1])
            resp = {"error": "success", "request_id": msg["request_id"], "data": data}
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()

    async def stop(self):
        self._server.close()
        await self._server.wait_closed()


@pytest.fixture
async def fake_mpv(tmp_path):
    server = FakeMpv(tmp_path / "mpv.sock")
    await server.start()
    yield server
    await server.stop()


PLAYER = PlayerConfig()
URL = "https://www.youtube.com/watch?v=abc123"


async def test_enqueue_appends_over_ipc(fake_mpv):
    ctrl = MpvController(socket_path=fake_mpv.socket_path)
    appended = await ctrl.enqueue(URL, PLAYER)
    assert appended is True
    assert ["loadfile", URL, "append-play"] in fake_mpv.commands
    await ctrl.close()


async def test_play_replaces_over_ipc(fake_mpv):
    ctrl = MpvController(socket_path=fake_mpv.socket_path)
    await ctrl.play(URL, PLAYER)
    assert ["loadfile", URL, "replace"] in fake_mpv.commands
    await ctrl.close()


async def test_pause_and_next(fake_mpv):
    ctrl = MpvController(socket_path=fake_mpv.socket_path)
    assert await ctrl.pause_toggle() is True
    assert await ctrl.playlist_next() is True
    assert ["cycle", "pause"] in fake_mpv.commands
    assert ["playlist-next"] in fake_mpv.commands
    await ctrl.close()


async def test_status_reports_queue(fake_mpv):
    ctrl = MpvController(socket_path=fake_mpv.socket_path)
    status = await ctrl.status()
    assert status is not None
    assert status.paused is False
    assert status.queued == 2  # 3 entries, playing index 0
    await ctrl.close()


async def test_dead_socket_reports_not_alive(tmp_path):
    ctrl = MpvController(socket_path=str(tmp_path / "nothing.sock"))
    assert await ctrl.is_alive() is False
    assert await ctrl.status() is None
    assert await ctrl.pause_toggle() is False
    await ctrl.close()


async def test_play_with_start_over_ipc(fake_mpv):
    ctrl = MpvController(socket_path=fake_mpv.socket_path)
    await ctrl.play(URL, PLAYER, start=42)
    assert any(
        cmd[0] == "loadfile" and "start=42" in cmd for cmd in fake_mpv.commands
    )
    await ctrl.close()


async def test_playback_snapshot(fake_mpv):
    fake_mpv.properties.update(
        {"path": URL, "time-pos": 12.5, "duration": 600.0, "media-title": "Some video"}
    )
    ctrl = MpvController(socket_path=fake_mpv.socket_path)
    assert await ctrl.playback_snapshot() == (URL, "Some video", 12.5, 600.0)

    fake_mpv.properties["duration"] = None  # live stream: no duration
    assert await ctrl.playback_snapshot() is None
    await ctrl.close()


async def test_set_speed_and_seek_over_ipc(fake_mpv):
    ctrl = MpvController(socket_path=fake_mpv.socket_path)
    assert await ctrl.set_speed(1.5) is True
    assert await ctrl.seek_absolute(123.0) is True
    assert ["set_property", "speed", 1.5] in fake_mpv.commands
    assert ["seek", 123.0, "absolute+exact"] in fake_mpv.commands
    await ctrl.close()


async def test_speed_reads_property(fake_mpv):
    fake_mpv.properties["speed"] = 1.75
    ctrl = MpvController(socket_path=fake_mpv.socket_path)
    assert await ctrl.speed() == 1.75
    status = await ctrl.status()
    assert status is not None
    assert status.speed == 1.75
    await ctrl.close()


async def test_cycle_subtitles_returns_label(fake_mpv):
    fake_mpv.properties["current-tracks/sub"] = {"lang": "en", "title": "English"}
    ctrl = MpvController(socket_path=fake_mpv.socket_path)
    assert await ctrl.cycle_subtitles() == "English"
    assert ["cycle", "sub"] in fake_mpv.commands

    fake_mpv.properties["current-tracks/sub"] = None
    assert await ctrl.cycle_subtitles() == "off"
    await ctrl.close()


def test_build_command_flags(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/mpv")
    cmd = build_command(URL, PLAYER, ipc_socket="/tmp/s.sock")
    assert cmd[0] == "mpv"
    assert "--input-ipc-server=/tmp/s.sock" in cmd
    assert f"--ytdl-format={PLAYER.format}" in cmd
    assert cmd[-1] == URL

    audio_cmd = build_command(URL, PLAYER, audio_only=True)
    assert "--no-video" in audio_cmd
    assert not any(a.startswith("--ytdl-format") for a in audio_cmd)


def test_build_command_subtitle_flags(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/mpv")
    cmd = build_command(URL, PLAYER)  # subtitle_langs defaults to "fr,en"
    assert "--ytdl-raw-options-append=sub-langs=fr,en" in cmd
    assert "--ytdl-raw-options-append=write-subs=" in cmd
    assert "--ytdl-raw-options-append=write-auto-subs=" in cmd

    audio_cmd = build_command(URL, PLAYER, audio_only=True)
    assert not any("sub-langs" in a for a in audio_cmd)

    no_subs = build_command(URL, PlayerConfig(subtitle_langs=""))
    assert not any("sub-langs" in a or "write-subs" in a for a in no_subs)


def test_build_command_with_start(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/mpv")
    cmd = build_command(URL, PLAYER, start=123.4)
    # --start must be a per-file option, not global (would leak to IPC-loaded files).
    assert cmd[-4:] == ["--{", "--start=123", URL, "--}"]
    assert not any(a.startswith("--start") for a in build_command(URL, PLAYER))
    assert not any(a.startswith("--start") for a in build_command(URL, PLAYER, start=0))


def test_build_command_missing_mpv(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    with pytest.raises(PlayerError):
        build_command(URL, PLAYER)
