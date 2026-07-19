"""Desktop notifications for live streams (detection happens on the server).

Notifications go through `notify-send`; the click action is read back via
--wait, which blocks — call send_live_notification from a thread worker.
"""

from __future__ import annotations

import shutil
import subprocess

from .models import Video


def send_live_notification(video: Video) -> str:
    """Blocking desktop notification.

    Returns the clicked action: "watch" (play in mpv), "default" (open in
    ytui), or "" if the notification was dismissed or actions are unsupported.
    """
    if shutil.which("notify-send") is None:
        return ""
    if video.platform == "twitch":
        summary = f"🟣 Twitch — {video.channel_title or 'Twitch'}"
    else:
        summary = f"🔴 Live — {video.channel_title or 'YouTube'}"
    cmd = [
        "notify-send",
        "--app-name=ytui",
        "--icon=video-display",
        "--wait",
        "--action=watch=▶ Regarder",
        "--action=default=Ouvrir dans ytui",
        summary,
        video.title,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return ""
    if res.returncode != 0:
        # Old libnotify without --wait/--action: fire-and-forget fallback.
        plain = [a for a in cmd if a != "--wait" and not a.startswith("--action=")]
        subprocess.run(plain, capture_output=True, text=True)
        return ""
    return res.stdout.strip()
