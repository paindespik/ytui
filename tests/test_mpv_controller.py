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
                data = {"playlist-count": 3, "playlist-pos": 0, "pause": False}.get(
                    msg["command"][1]
                )
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


def test_build_command_missing_mpv(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    with pytest.raises(PlayerError):
        build_command(URL, PLAYER)
