"""YouTube OAuth2 (Desktop app flow) and write actions: like, comment.

Google client libraries are optional; install with `pip install 'ytui[auth]'`.
All functions here are blocking (network, browser consent) — call them from a
thread worker, never from the event loop.
"""

from __future__ import annotations

from pathlib import Path

from .config import Config, config_dir

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

INSTALL_HINT = "pip install 'ytui[auth]'"


class AuthError(Exception):
    """OAuth setup/consent problem, with a user-readable message."""


class ApiError(Exception):
    """YouTube API call failed, with a user-readable message."""


def token_path() -> Path:
    return config_dir() / "oauth_token.json"


def client_secret_path(config: Config) -> Path:
    if config.auth.client_secret:
        return Path(config.auth.client_secret).expanduser()
    return config_dir() / "client_secret.json"


def _import_google():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise AuthError(
            f"Google API libraries are missing. Install them with: {INSTALL_HINT}"
        ) from exc
    return Request, Credentials, InstalledAppFlow, build


def get_youtube_client(config: Config):
    """Return an authenticated YouTube API client, running the OAuth flow if needed.

    Blocking: may refresh a token over the network or open a browser for consent.
    """
    Request, Credentials, InstalledAppFlow, build = _import_google()

    creds = None
    tok = token_path()
    if tok.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(tok), SCOPES)
        except (ValueError, KeyError):
            creds = None  # corrupt/old token: re-run the flow

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None  # refresh token revoked/expired: re-run the flow

    if not creds or not creds.valid:
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
        _save_token(tok, creds.to_json())

    return build("youtube", "v3", credentials=creds)


def _save_token(path: Path, token_json: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token_json, encoding="utf-8")
    path.chmod(0o600)


def like_video(youtube, video_id: str) -> None:
    """Rate a video 'like' on behalf of the authenticated user."""
    try:
        youtube.videos().rate(id=video_id, rating="like").execute()
    except Exception as exc:
        raise ApiError(_api_message(exc)) from exc


def post_comment(youtube, video_id: str, text: str) -> None:
    """Post a top-level comment on a video."""
    body = {
        "snippet": {
            "videoId": video_id,
            "topLevelComment": {"snippet": {"textOriginal": text}},
        }
    }
    try:
        youtube.commentThreads().insert(part="snippet", body=body).execute()
    except Exception as exc:
        raise ApiError(_api_message(exc)) from exc


def _api_message(exc: Exception) -> str:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == 403:
        return f"YouTube refused the request (quota exceeded or action not allowed): {exc}"
    return f"YouTube API error: {exc}"
