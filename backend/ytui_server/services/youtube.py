"""YouTube account actions (like/comment) using a pushed OAuth token.

The consent flow runs on the desktop client; the resulting oauth_token.json is
uploaded via POST /api/auth/youtube/token. The server only refreshes it.
All Google calls are blocking — callers wrap them in a thread.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

INSTALL_HINT = "pip install 'ytui-server[auth]'"

log = logging.getLogger(__name__)


class AuthError(Exception):
    """OAuth/token problem, with a user-readable message."""


class ApiError(Exception):
    """YouTube API call failed, with a user-readable message."""


def _import_google():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise AuthError(
            f"Google API libraries are missing. Install them with: {INSTALL_HINT}"
        ) from exc
    return Request, Credentials, build


class YouTubeService:
    def __init__(self, data_dir: Path) -> None:
        self.token_path = data_dir / "oauth_token.json"

    def is_authenticated(self) -> bool:
        return self.token_path.exists()

    def save_token(self, token_json: str) -> None:
        try:
            payload = json.loads(token_json)
        except json.JSONDecodeError as exc:
            raise AuthError(f"Invalid token JSON: {exc}") from exc
        if not isinstance(payload, dict) or "refresh_token" not in payload:
            raise AuthError("Token JSON must contain a refresh_token")
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(token_json, encoding="utf-8")
        self.token_path.chmod(0o600)

    def _client(self):
        """Build an authenticated YouTube API client (blocking)."""
        Request, Credentials, build = _import_google()
        if not self.token_path.exists():
            raise AuthError(
                "No OAuth token on the server. Push one with `ytui auth push` "
                "(POST /api/auth/youtube/token)."
            )
        try:
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
        except (ValueError, KeyError) as exc:
            raise AuthError(f"Stored OAuth token is invalid: {exc}") from exc
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                log.warning("youtube oauth token refresh failed: %s", exc)
                raise AuthError(f"OAuth token refresh failed: {exc}") from exc
            self.save_token(creds.to_json())
        if not creds.valid:
            raise AuthError("OAuth token is not valid; push a fresh one.")
        return build("youtube", "v3", credentials=creds)

    def like_video(self, video_id: str) -> None:
        """Rate a video 'like' on behalf of the authenticated user (blocking)."""
        youtube = self._client()
        try:
            youtube.videos().rate(id=video_id, rating="like").execute()
        except Exception as exc:
            log.warning("youtube like_video failed for %s: %s", video_id, exc)
            raise ApiError(_api_message(exc)) from exc

    def post_comment(self, video_id: str, text: str) -> None:
        """Post a top-level comment on a video (blocking)."""
        youtube = self._client()
        body = {
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text}},
            }
        }
        try:
            youtube.commentThreads().insert(part="snippet", body=body).execute()
        except Exception as exc:
            log.warning("youtube post_comment failed for %s: %s", video_id, exc)
            raise ApiError(_api_message(exc)) from exc


def _api_message(exc: Exception) -> str:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == 403:
        return f"YouTube refused the request (quota exceeded or action not allowed): {exc}"
    return f"YouTube API error: {exc}"
