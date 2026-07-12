"""Desktop notifications for live streams (detection happens on the server).

Notifications go through `notify-send`; the click action is read back via
--wait, which blocks — call send_live_notification from a thread worker.
"""

from __future__ import annotations

import shutil
import subprocess

from .models import Video


def send_live_notification(video: Video) -> bool:
    """Blocking desktop notification. True if the user clicked the action."""
    if shutil.which("notify-send") is None:
        return False
    cmd = [
        "notify-send",
        "--app-name=ytui",
        "--icon=video-display",
        "--wait",
        "--action=default=Ouvrir dans ytui",
        f"🔴 Live — {video.channel_title or 'YouTube'}",
        video.title,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return False
    if res.returncode != 0:
        # Old libnotify without --wait/--action: fire-and-forget fallback.
        subprocess.run(cmd[:3] + cmd[5:], capture_output=True, text=True)
        return False
    return bool(res.stdout.strip())
