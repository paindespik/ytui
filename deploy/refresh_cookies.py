#!/usr/bin/env python3
"""Re-push the dedicated browser's YouTube session to the local backend.

YouTube ties a signed-in session to a `__Secure-1PSIDTS` cookie that only its
holder may renew: youtube.com never sends a fresh one back, and Google's
rotation endpoint refuses third-party callers. The backend therefore cannot keep
a pushed session alive on its own.

So a headless Firefox with a dedicated profile holds the session and keeps
renewing it (ytui-browser.service), and this script hands the current cookies to
the backend. Reading beats guessing: the profile is always the fresh copy.

Stdlib only, so the host needs no Python packages.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

PROFILE = Path(os.environ.get("YTUI_COOKIE_PROFILE", "~/ytui-cookies/profile")).expanduser()
BACKEND = os.environ.get("YTUI_BACKEND_URL", "http://127.0.0.1:8776")
ENV_FILE = Path(os.environ.get("YTUI_ENV_FILE", "~/ytui-deploy/deploy/.env")).expanduser()


def api_token() -> str:
    token = os.environ.get("YTUI_API_TOKEN")
    if token:
        return token
    if not ENV_FILE.exists():
        sys.exit(f"No YTUI_API_TOKEN and no {ENV_FILE}")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "YTUI_API_TOKEN":
            return value.strip().strip("'\"")
    sys.exit(f"YTUI_API_TOKEN not found in {ENV_FILE}")


def read_cookies(profile: Path) -> str:
    """youtube.com cookies of a live Firefox profile, in Netscape format.

    The database is copied first: Firefox holds it open, and its -wal file
    carries the most recent writes.
    """
    source = profile / "cookies.sqlite"
    if not source.exists():
        sys.exit(f"No cookies.sqlite in {profile}: sign in to YouTube in that profile first")
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "cookies.sqlite"
        shutil.copy2(source, copy)
        # Only the -wal: a copied -shm can disagree with it, and SQLite then
        # needs to recover the log — which a read-only connection refuses. The
        # copy is ours, so open it writable and let recovery replay the WAL,
        # where the freshest __Secure-1PSIDTS usually still sits.
        wal = source.with_name(source.name + "-wal")
        if wal.exists():
            shutil.copy2(wal, copy.with_name(copy.name + "-wal"))
        con = sqlite3.connect(copy)
        try:
            rows = con.execute(
                "SELECT host, path, isSecure, expiry, name, value FROM moz_cookies "
                "WHERE host LIKE '%youtube.com'"
            ).fetchall()
        finally:
            con.close()

    lines = ["# Netscape HTTP Cookie File", "# Written by ytui refresh_cookies.py", ""]
    for host, path, secure, expiry, name, value in rows:
        lines.append(
            "\t".join(
                (
                    host,
                    "TRUE" if host.startswith(".") else "FALSE",
                    path or "/",
                    "TRUE" if secure else "FALSE",
                    str(int(expiry or 0)),
                    name,
                    value,
                )
            )
        )
    names = {r[4] for r in rows}
    missing = {"LOGIN_INFO", "__Secure-1PSIDTS"} - names
    if missing:
        sys.exit(
            f"Profile {profile} holds no usable session (missing {', '.join(sorted(missing))}): "
            "sign in to YouTube in that profile"
        )
    return "\n".join(lines) + "\n"


def push(text: str, token: str) -> None:
    request = urllib.request.Request(
        f"{BACKEND}/api/auth/youtube/cookies",
        data=text.encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            if resp.status != 204:
                sys.exit(f"Unexpected status {resp.status}")
    except urllib.error.HTTPError as exc:
        # A rejected candidate leaves the working session in place, so a failed
        # refresh is a warning, not an outage.
        sys.exit(f"Backend refused the cookies ({exc.code}): {exc.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as exc:
        sys.exit(f"Backend unreachable at {BACKEND}: {exc.reason}")


def snapshot(text: str) -> None:
    """Keep the last session the backend accepted.

    If the browser ever loses its session (a crash landing on a signed-out
    youtube.com wipes the .youtube.com cookies), this file restores service
    without redoing the interactive sign-in:
        curl -H "Authorization: Bearer $TOKEN" --data-binary @last-good.txt \\
             -H 'Content-Type: text/plain' http://127.0.0.1:8776/api/auth/youtube/cookies
    """
    path = PROFILE.parent / "last-good.txt"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def main() -> None:
    text = read_cookies(PROFILE)
    push(text, api_token())
    snapshot(text)
    print(f"Pushed {text.count(chr(10)) - 3} youtube.com cookies from {PROFILE}")


if __name__ == "__main__":
    main()
