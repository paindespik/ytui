"""YouTube auth status, token upload, rating and comments with a mocked service."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from ytui_server.models import CommentOut, CommentsResponse
from ytui_server.services.youtube import (
    ApiError,
    AuthError,
    _is_comments_disabled,
    _thread_comment,
)

VALID_TOKEN = json.dumps(
    {
        "token": "ya29.x",
        "refresh_token": "1//refresh",
        "client_id": "id",
        "client_secret": "secret",
        "scopes": ["https://www.googleapis.com/auth/youtube.force-ssl"],
    }
)


def test_status_unauthenticated(client):
    assert client.get("/api/auth/youtube/status").json() == {"authenticated": False}


def test_push_token_and_status(client, app, settings):
    resp = client.post("/api/auth/youtube/token", content=VALID_TOKEN)
    assert resp.status_code == 204
    assert client.get("/api/auth/youtube/status").json() == {"authenticated": True}
    token_file = settings.data_dir / "oauth_token.json"
    assert token_file.exists()
    assert (token_file.stat().st_mode & 0o777) == 0o600


def test_push_invalid_json(client):
    resp = client.post("/api/auth/youtube/token", content="not json")
    assert resp.status_code == 422


def test_push_token_without_refresh_token(client):
    resp = client.post("/api/auth/youtube/token", content=json.dumps({"token": "x"}))
    assert resp.status_code == 422


def test_like_without_token(client, app):
    app.state.youtube_service.rate_video = MagicMock(
        side_effect=AuthError("No OAuth token on the server.")
    )
    resp = client.post("/api/videos/vid000000001/like")
    assert resp.status_code == 409


def test_like_success(client, app):
    app.state.youtube_service.rate_video = MagicMock()
    resp = client.post("/api/videos/vid000000001/like")
    assert resp.status_code == 204
    app.state.youtube_service.rate_video.assert_called_once_with("vid000000001", "like")


def test_unlike_passes_rating_none(client, app):
    app.state.youtube_service.rate_video = MagicMock()
    resp = client.post("/api/videos/vid000000001/like?rating=none")
    assert resp.status_code == 204
    app.state.youtube_service.rate_video.assert_called_once_with("vid000000001", "none")


def test_like_rejects_unknown_rating(client, app):
    app.state.youtube_service.rate_video = MagicMock()
    assert client.post("/api/videos/vid000000001/like?rating=dislike").status_code == 422
    app.state.youtube_service.rate_video.assert_not_called()


def test_like_api_error(client, app):
    app.state.youtube_service.rate_video = MagicMock(side_effect=ApiError("quota"))
    resp = client.post("/api/videos/vid000000001/like")
    assert resp.status_code == 502


def test_rating_reports_current_state(client, app):
    app.state.youtube_service.get_rating = MagicMock(return_value="like")
    resp = client.get("/api/videos/vid000000001/rating")
    assert resp.status_code == 200
    assert resp.json() == {"rating": "like"}


def test_rating_without_token(client, app):
    app.state.youtube_service.get_rating = MagicMock(side_effect=AuthError("no token"))
    assert client.get("/api/videos/vid000000001/rating").status_code == 409


def test_rating_unsupported_platform(client, app):
    app.state.youtube_service.get_rating = MagicMock()
    resp = client.get("/api/videos/abc/rating?platform=bitchute")
    assert resp.status_code == 409
    app.state.youtube_service.get_rating.assert_not_called()


def test_comment_returns_created_comment(client, app):
    app.state.youtube_service.post_comment = MagicMock(
        return_value=CommentOut(
            comment_id="c1", text="Nice!", channel_name="Me", timestamp=1700000000
        )
    )
    resp = client.post("/api/videos/vid000000001/comment", json={"text": "Nice!"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["comment_id"] == "c1"
    assert body["text"] == "Nice!"
    assert body["channel_name"] == "Me"
    app.state.youtube_service.post_comment.assert_called_once_with("vid000000001", "Nice!")


def test_comment_without_token(client, app):
    app.state.youtube_service.post_comment = MagicMock(side_effect=AuthError("no token"))
    resp = client.post("/api/videos/vid000000001/comment", json={"text": "Nice!"})
    assert resp.status_code == 409


def test_youtube_comments_listing(client, app):
    page = CommentsResponse(
        items=[CommentOut(comment_id="c1", text="hello", channel_name="Someone")],
        total=42,
        next_cursor="TOKEN2",
    )
    app.state.youtube_service.list_comments = MagicMock(return_value=page)
    resp = client.get("/api/videos/vid000000001/comments?page_size=20")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 42
    assert body["next_cursor"] == "TOKEN2"
    assert body["items"][0]["text"] == "hello"
    app.state.youtube_service.list_comments.assert_called_once_with(
        "vid000000001", cursor=None, limit=20
    )


def test_youtube_comments_forward_cursor(client, app):
    app.state.youtube_service.list_comments = MagicMock(return_value=CommentsResponse(items=[]))
    assert client.get("/api/videos/vid000000001/comments?cursor=TOKEN2").status_code == 200
    app.state.youtube_service.list_comments.assert_called_once_with(
        "vid000000001", cursor="TOKEN2", limit=50
    )


def test_youtube_comments_disabled(client, app):
    app.state.youtube_service.list_comments = MagicMock(
        return_value=CommentsResponse(items=[], disabled=True)
    )
    body = client.get("/api/videos/vid000000001/comments").json()
    assert body["disabled"] is True
    assert body["items"] == []


def test_youtube_comments_without_token(client, app):
    app.state.youtube_service.list_comments = MagicMock(side_effect=AuthError("no token"))
    assert client.get("/api/videos/vid000000001/comments").status_code == 409


def test_thread_comment_mapping():
    thread = {
        "id": "thread1",
        "snippet": {
            "totalReplyCount": 3,
            "topLevelComment": {
                "id": "comment1",
                "snippet": {
                    "authorDisplayName": "@someone",
                    "textOriginal": "hello",
                    "publishedAt": "2026-08-01T12:00:00Z",
                    "likeCount": 7,
                },
            },
        },
    }
    comment = _thread_comment(thread)
    assert comment.comment_id == "comment1"
    assert comment.channel_name == "@someone"
    assert comment.text == "hello"
    assert comment.likes == 7
    assert comment.replies == 3
    # epoch seconds, the format every client formats dates from
    assert comment.timestamp == 1785585600


def test_thread_comment_tolerates_missing_fields():
    comment = _thread_comment({"id": "thread1", "snippet": {}})
    assert comment.comment_id == "thread1"
    assert comment.text == ""
    assert comment.timestamp is None


class _FakeHttpError(Exception):
    """Duck-typed googleapiclient.HttpError: only .resp.status and .content are read."""

    def __init__(self, status: int, content: bytes) -> None:
        super().__init__("boom")
        self.resp = SimpleNamespace(status=status)
        self.content = content


def test_comments_disabled_detection():
    disabled = _FakeHttpError(403, b'{"error":{"errors":[{"reason":"commentsDisabled"}]}}')
    assert _is_comments_disabled(disabled)
    quota = _FakeHttpError(403, b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}')
    assert not _is_comments_disabled(quota)
    assert not _is_comments_disabled(Exception("network down"))
