"""Local YouTube OAuth2 consent flow (Desktop app), for `ytui auth push`.

The browser consent runs on this machine; the resulting token is then pushed
to the backend server, which performs the actual like/comment calls.
Google client libraries are optional; install with `pip install 'ytui[auth]'`.
"""

from __future__ import annotations

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
