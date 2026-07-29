"""Background video download via the yt-dlp library (blocking; run in a worker thread)."""

from __future__ import annotations

from pathlib import Path

from ..config import PlayerConfig
from .mpv import ytdl_format


def download_video(url: str, player: PlayerConfig) -> Path:
    """Download the given URL into player.download_dir. Returns the target directory."""
    import yt_dlp

    target = Path(player.download_dir).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "outtmpl": str(target / "%(title)s [%(id)s].%(ext)s"),
    }
    opts["format"] = ytdl_format(player.max_height)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return target
