"""Launch mpv as a detached external window."""

from __future__ import annotations

import shutil
import subprocess

from ..config import PlayerConfig


class PlayerError(Exception):
    pass


def play(url: str, player: PlayerConfig) -> None:
    """Start mpv detached on the given URL. Raises PlayerError if mpv is missing."""
    if shutil.which(player.command) is None:
        raise PlayerError(
            f"Player '{player.command}' not found. Install mpv (e.g. 'sudo apt install mpv') "
            "or set [player].command in the config."
        )
    cmd = [player.command, "--no-terminal", "--force-window=immediate"]
    if player.audio_only:
        cmd.append("--no-video")
    elif player.format:
        cmd.append(f"--ytdl-format={player.format}")
    cmd.append(url)
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
