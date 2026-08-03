"""YouTube account actions (rate, read/post comments) using a pushed OAuth token.

The consent flow runs on the desktop client; the resulting oauth_token.json is
uploaded via POST /api/auth/youtube/token. The server only refreshes it.
All Google calls are blocking — callers wrap them in a thread.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from ..models import CommentOut, CommentsResponse

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

    def rate_video(self, video_id: str, rating: str = "like") -> None:
        """Rate a video on behalf of the authenticated user (blocking).

        `rating="none"` removes an existing like, so the clients can toggle.
        """
        youtube = self._client()
        try:
            youtube.videos().rate(id=video_id, rating=rating).execute()
        except Exception as exc:
            log.warning("youtube rate_video(%s) failed for %s: %s", rating, video_id, exc)
            raise ApiError(_api_message(exc)) from exc

    def get_rating(self, video_id: str) -> str:
        """The authenticated user's rating: "like", "dislike" or "none" (blocking)."""
        youtube = self._client()
        try:
            response = youtube.videos().getRating(id=video_id).execute()
        except Exception as exc:
            log.warning("youtube get_rating failed for %s: %s", video_id, exc)
            raise ApiError(_api_message(exc)) from exc
        items = response.get("items") or []
        rating = items[0].get("rating") if items else "none"
        return rating if rating in ("like", "dislike", "none") else "none"

    def post_comment(self, video_id: str, text: str) -> CommentOut:
        """Post a top-level comment and return it as stored (blocking)."""
        youtube = self._client()
        body = {
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text}},
            }
        }
        try:
            created = youtube.commentThreads().insert(part="snippet", body=body).execute()
        except Exception as exc:
            log.warning("youtube post_comment failed for %s: %s", video_id, exc)
            raise ApiError(_write_message(exc)) from exc
        return _thread_comment(created)

    def list_comments(
        self, video_id: str, cursor: str | None = None, limit: int = 50
    ) -> CommentsResponse:
        """Top-level comments, most relevant first (blocking).

        `cursor` is an opaque API page token; the reply count is reported but
        replies themselves are not fetched.
        """
        youtube = self._client()
        try:
            response = (
                youtube.commentThreads()
                .list(
                    part="snippet",
                    videoId=video_id,
                    maxResults=limit,
                    order="relevance",
                    textFormat="plainText",
                    pageToken=cursor or None,
                )
                .execute()
            )
        except Exception as exc:
            if _is_comments_disabled(exc):
                return CommentsResponse(items=[], disabled=True)
            log.warning("youtube list_comments failed for %s: %s", video_id, exc)
            raise ApiError(_api_message(exc)) from exc
        return CommentsResponse(
            items=[_thread_comment(thread) for thread in response.get("items") or []],
            # The thread listing has no grand total: only the first page pays
            # for the extra statistics call, later pages keep the client's count.
            total=0 if cursor else self._comment_count(youtube, video_id),
            next_cursor=response.get("nextPageToken"),
        )

    def list_replies(
        self, comment_id: str, cursor: str | None = None, limit: int = 50
    ) -> CommentsResponse:
        """Replies to one top-level comment, oldest first (blocking).

        `comments.list` reports no grand total, so `total` stays 0: clients keep
        the parent's `replies` count.
        """
        youtube = self._client()
        try:
            response = (
                youtube.comments()
                .list(
                    part="snippet",
                    parentId=comment_id,
                    maxResults=limit,
                    textFormat="plainText",
                    pageToken=cursor or None,
                )
                .execute()
            )
        except Exception as exc:
            if _is_comments_disabled(exc):
                return CommentsResponse(items=[], disabled=True)
            log.warning("youtube list_replies failed for %s: %s", comment_id, exc)
            raise ApiError(_api_message(exc)) from exc
        return CommentsResponse(
            items=[_reply_comment(c) for c in response.get("items") or []],
            next_cursor=response.get("nextPageToken"),
        )

    def post_reply(self, comment_id: str, text: str) -> CommentOut:
        """Reply to a top-level comment and return it as stored (blocking)."""
        youtube = self._client()
        body = {"snippet": {"parentId": comment_id, "textOriginal": text}}
        try:
            created = youtube.comments().insert(part="snippet", body=body).execute()
        except Exception as exc:
            log.warning("youtube post_reply failed for %s: %s", comment_id, exc)
            raise ApiError(_write_message(exc)) from exc
        return _reply_comment(created)

    @staticmethod
    def _comment_count(youtube, video_id: str) -> int:
        """Total comment count from the video statistics, 0 when unavailable."""
        try:
            response = youtube.videos().list(part="statistics", id=video_id).execute()
            items = response.get("items") or []
            return int(items[0]["statistics"]["commentCount"]) if items else 0
        except Exception as exc:
            log.info("youtube comment count unavailable for %s: %s", video_id, exc)
            return 0


def _thread_comment(thread: dict) -> CommentOut:
    """Map a commentThread resource onto the shared CommentOut shape."""
    outer = thread.get("snippet") or {}
    snippet = (outer.get("topLevelComment") or {}).get("snippet") or {}
    return CommentOut(
        comment_id=(outer.get("topLevelComment") or {}).get("id") or thread.get("id", ""),
        text=snippet.get("textOriginal") or snippet.get("textDisplay") or "",
        channel_name=snippet.get("authorDisplayName") or "",
        timestamp=_epoch(snippet.get("publishedAt")),
        replies=int(outer.get("totalReplyCount") or 0),
        likes=int(snippet.get("likeCount") or 0),
    )


def _reply_comment(comment: dict) -> CommentOut:
    """Map a raw `comment` resource (comments.list / comments.insert).

    Unlike a commentThread the snippet sits at the top level, and a reply has
    no reply count of its own.
    """
    snippet = comment.get("snippet") or {}
    return CommentOut(
        comment_id=comment.get("id") or "",
        text=snippet.get("textOriginal") or snippet.get("textDisplay") or "",
        channel_name=snippet.get("authorDisplayName") or "",
        timestamp=_epoch(snippet.get("publishedAt")),
        likes=int(snippet.get("likeCount") or 0),
    )


def _epoch(published_at: str | None) -> int | None:
    """RFC-3339 publication date → epoch seconds (the clients' comment format)."""
    if not published_at:
        return None
    try:
        return int(datetime.fromisoformat(published_at.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _is_comments_disabled(exc: Exception) -> bool:
    """403 raised because the uploader turned comments off (not a real failure)."""
    return _status(exc) == 403 and "commentsDisabled" in _content(exc)


def _write_message(exc: Exception) -> str:
    """Message for a failed comment write.

    YouTube answers 400 `processingFailure` for a well-formed request it simply
    declines — a thread closed to new replies, or its spam heuristics. Observed
    on most popular videos' top comments, so it must not surface as a raw
    HttpError dump.
    """
    if _status(exc) == 400 and "processingFailure" in _content(exc):
        return "YouTube declined the comment (this thread may not accept new replies)"
    return _api_message(exc)


def _status(exc: Exception) -> int | None:
    return getattr(getattr(exc, "resp", None), "status", None)


def _content(exc: Exception) -> str:
    content = getattr(exc, "content", b"") or b""
    if isinstance(content, bytes):
        content = content.decode("utf-8", "replace")
    return content


def _api_message(exc: Exception) -> str:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == 403:
        return f"YouTube refused the request (quota exceeded or action not allowed): {exc}"
    return f"YouTube API error: {exc}"
