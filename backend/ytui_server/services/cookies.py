"""Account cookies pushed from the desktop client.

Used ONLY for the personalised YouTube home feed (yt-dlp `:ytrec`): search,
stream resolution and details stay anonymous, so a flagged session cannot break
playback. yt-dlp rewrites rotated cookies back into this file on every use,
which is what keeps the pushed session alive.
"""

from __future__ import annotations

import http.cookiejar
import logging
import os
import time
from pathlib import Path

# What yt-dlp itself treats as a signed-in YouTube session
# (yt_dlp/extractor/youtube/_base.py).
REQUIRED_SID = ("SAPISID", "__Secure-3PAPISID", "__Secure-1PAPISID")

log = logging.getLogger(__name__)


class CookieError(Exception):
    """Rejected cookie payload, with a user-readable message."""


class CookieStore:
    """The single Netscape cookie file yt-dlp reads (and rewrites) for the home feed."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "youtube_cookies.txt"

    def exists(self) -> bool:
        return self.path.exists()

    def stage(self, netscape_text: str) -> Path:
        """Validate a candidate jar and return its path, leaving the live one alone.

        Callers probe the staged file against YouTube before `commit`, so a
        refresh that Google refuses never destroys a session that still works.
        Raises CookieError with a user-readable message.
        """
        tmp = self.path.with_suffix(".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(netscape_text, encoding="utf-8")
        tmp.chmod(0o600)
        try:
            _validate(tmp)
        except CookieError:
            tmp.unlink(missing_ok=True)
            raise
        return tmp

    def commit(self, staged: Path) -> None:
        """Atomically promote a staged jar to the live one."""
        os.replace(staged, self.path)
        self.path.chmod(0o600)

    def discard(self, staged: Path) -> None:
        """Drop a rejected candidate; the live jar is untouched."""
        staged.unlink(missing_ok=True)

    def clear(self) -> bool:
        """Remove the stored cookies; True when a file was actually removed."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True


def _validate(path: Path) -> None:
    from yt_dlp.cookies import YoutubeDLCookieJar

    jar = YoutubeDLCookieJar(str(path))
    try:
        # ignore_expires so we can report *which* cookie died instead of a
        # generic empty jar.
        jar.load(ignore_discard=True, ignore_expires=True)
    except http.cookiejar.LoadError as exc:
        log.info("rejected cookie upload: %s", exc)
        raise CookieError(
            "Not a Netscape cookie file (expected the tab-separated cookies.txt format)"
        ) from exc
    except OSError as exc:
        raise CookieError(f"Could not read the cookie file: {exc}") from exc

    youtube = [c for c in jar if (c.domain or "").endswith("youtube.com")]
    if not youtube:
        raise CookieError("No youtube.com cookies in the file")
    by_name = {c.name: c for c in youtube}
    if "LOGIN_INFO" not in by_name:
        raise CookieError(
            "No YouTube session: the LOGIN_INFO cookie is missing "
            "(export while signed in to youtube.com)"
        )
    if not any(name in by_name for name in REQUIRED_SID):
        raise CookieError("No YouTube session: the SAPISID cookie is missing")
    now = time.time()
    for name in ("LOGIN_INFO", *REQUIRED_SID):
        cookie = by_name.get(name)
        if cookie is not None and cookie.expires and cookie.expires < now:
            raise CookieError(f"Cookie {name} expired; export a fresh session")
