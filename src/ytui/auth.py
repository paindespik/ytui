"""Local YouTube credentials for `ytui auth push` / `ytui auth cookies`.

The browser consent runs on this machine; the resulting token is then pushed
to the backend server, which performs the actual like/comment calls.
Google client libraries are optional; install with `pip install 'ytui[auth]'`.
Cookie export only needs yt-dlp, which is a hard dependency.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .config import Config, config_dir

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

INSTALL_HINT = "pip install 'ytui[auth]'"


class AuthError(Exception):
    """OAuth setup/consent problem, with a user-readable message."""


def token_path() -> Path:
    return config_dir() / "oauth_token.json"


def client_secret_path(config: Config) -> Path:
    if config.auth.client_secret:
        return Path(config.auth.client_secret).expanduser()
    return config_dir() / "client_secret.json"


def run_consent_flow(config: Config) -> str:
    """Run the local browser OAuth flow and return the token JSON.

    Blocking: opens a browser for consent. The token is also cached locally.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise AuthError(
            f"Google API libraries are missing. Install them with: {INSTALL_HINT}"
        ) from exc

    secret = client_secret_path(config)
    if not secret.exists():
        raise AuthError(
            f"OAuth client secret not found: {secret}\n"
            "Download it from Google Cloud Console (OAuth client, type 'Desktop app'). "
            "See the README section 'YouTube account (like/comment)'."
        )
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
        creds = flow.run_local_server(port=0)
    except Exception as exc:
        raise AuthError(f"OAuth authorization failed: {exc}") from exc
    token_json = creds.to_json()
    _save_token(token_path(), token_json)
    return token_json


def _save_token(path: Path, token_json: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token_json, encoding="utf-8")
    path.chmod(0o600)


# Firefox forks yt-dlp does not know by name: same profile format, different
# root. Their profile lives in a directory yt-dlp accepts as a `profile` path.
_FIREFOX_FORKS = {"zen": Path("~/.zen")}


def _fork_profile(root: Path, wanted: str | None) -> str:
    """Resolve a Firefox-fork profile directory from its profiles.ini.

    Without `wanted`, prefer the profile the browser itself last installed to
    ([InstallXXX] Default=), because a fork's [Profile] Default=1 often points
    at an unused starter profile.
    """
    import configparser

    root = root.expanduser()
    ini = root / "profiles.ini"
    if not ini.exists():
        raise AuthError(f"No profiles.ini in {root}: is the browser installed?")
    parser = configparser.ConfigParser(strict=False)
    parser.read(ini, encoding="utf-8")

    profiles: dict[str, str] = {}  # name -> relative path
    fallback: str | None = None
    for section in parser.sections():
        if section.startswith("Profile"):
            path = parser[section].get("Path")
            if not path:
                continue
            profiles[parser[section].get("Name", path)] = path
            if parser[section].get("Default") == "1":
                fallback = path
        elif section.startswith("Install"):
            fallback = parser[section].get("Default") or fallback

    if wanted:
        path = profiles.get(wanted, wanted if wanted in profiles.values() else None)
        if path is None:
            known = ", ".join(sorted(profiles)) or "none"
            raise AuthError(f"Unknown profile {wanted!r} in {ini} (available: {known})")
    elif fallback:
        path = fallback
    else:
        raise AuthError(f"No default profile in {ini}; pass one explicitly")
    return str(root / path)


def export_browser_cookies(browser: str, profile: str | None = None) -> str:
    """Netscape cookie text holding only the youtube.com cookies of `browser`.

    Never returns the whole browser jar: the server has no business with
    unrelated sites' cookies.
    """
    from yt_dlp.cookies import YoutubeDLCookieJar, extract_cookies_from_browser

    label = browser
    fork_root = _FIREFOX_FORKS.get(browser.lower())
    if fork_root is not None:
        profile = _fork_profile(fork_root, profile)
        browser = "firefox"

    try:
        source = extract_cookies_from_browser(browser, profile)
    except Exception as exc:
        raise AuthError(f"Could not read {label} cookies: {exc}") from exc

    jar = YoutubeDLCookieJar()
    for cookie in source:
        if (cookie.domain or "").endswith("youtube.com"):
            jar.set_cookie(cookie)
    if not any(True for _ in jar):
        raise AuthError(
            f"No youtube.com cookies in the {label} profile (sign in to youtube.com first)"
        )

    fd, name = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    tmp = Path(name)
    try:
        jar.save(str(tmp), ignore_discard=True, ignore_expires=True)
        return tmp.read_text(encoding="utf-8")
    except Exception as exc:
        raise AuthError(f"Could not read {label} cookies: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
