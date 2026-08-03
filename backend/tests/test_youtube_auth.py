"""YouTube auth status, token/cookie upload, rating and comments with a mocked service."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_video

from ytui_server.models import CommentOut, CommentsResponse
from ytui_server.services.youtube import (
    ApiError,
    AuthError,
    _is_comments_disabled,
    _reply_comment,
    _thread_comment,
    _write_message,
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
    assert client.get("/api/auth/youtube/status").json() == {
        "authenticated": False,
        "cookies": False,
    }


def test_push_token_and_status(client, app, settings):
    resp = client.post("/api/auth/youtube/token", content=VALID_TOKEN)
    assert resp.status_code == 204
    assert client.get("/api/auth/youtube/status").json() == {
        "authenticated": True,
        "cookies": False,
    }
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


def test_youtube_replies_listing(client, app):
    app.state.youtube_service.list_replies = MagicMock(
        return_value=CommentsResponse(
            items=[CommentOut(comment_id="c1.r1", text="hi back", channel_name="Other")],
            next_cursor="TOK",
        )
    )
    resp = client.get("/api/videos/vid000000001/comments/cid1/replies?page_size=20")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["comment_id"] == "c1.r1"
    assert body["next_cursor"] == "TOK"
    # replies carry no grand total: clients keep the parent's count
    assert body["total"] == 0
    app.state.youtube_service.list_replies.assert_called_once_with(
        "cid1", cursor=None, limit=20
    )


def test_youtube_replies_forward_cursor(client, app):
    app.state.youtube_service.list_replies = MagicMock(return_value=CommentsResponse(items=[]))
    resp = client.get("/api/videos/vid000000001/comments/cid1/replies?cursor=TOK")
    assert resp.status_code == 200
    app.state.youtube_service.list_replies.assert_called_once_with(
        "cid1", cursor="TOK", limit=50
    )


def test_youtube_replies_without_token(client, app):
    app.state.youtube_service.list_replies = MagicMock(side_effect=AuthError("no token"))
    resp = client.get("/api/videos/vid000000001/comments/cid1/replies")
    assert resp.status_code == 409


def test_youtube_replies_api_error(client, app):
    app.state.youtube_service.list_replies = MagicMock(side_effect=ApiError("quota"))
    resp = client.get("/api/videos/vid000000001/comments/cid1/replies")
    assert resp.status_code == 502


def test_reply_returns_created_comment(client, app):
    app.state.youtube_service.post_reply = MagicMock(
        return_value=CommentOut(
            comment_id="cid1.r9", text="Merci !", channel_name="Me", timestamp=1700000000
        )
    )
    resp = client.post(
        "/api/videos/vid000000001/comments/cid1/reply", json={"text": "Merci !"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["comment_id"] == "cid1.r9"
    assert body["text"] == "Merci !"
    app.state.youtube_service.post_reply.assert_called_once_with("cid1", "Merci !")


def test_reply_without_token(client, app):
    app.state.youtube_service.post_reply = MagicMock(side_effect=AuthError("no token"))
    resp = client.post(
        "/api/videos/vid000000001/comments/cid1/reply", json={"text": "Merci !"}
    )
    assert resp.status_code == 409


def test_reply_api_error(client, app):
    app.state.youtube_service.post_reply = MagicMock(side_effect=ApiError("quota"))
    resp = client.post(
        "/api/videos/vid000000001/comments/cid1/reply", json={"text": "Merci !"}
    )
    assert resp.status_code == 502


def test_reply_unsupported_platform(client, app):
    app.state.youtube_service.post_reply = MagicMock()
    resp = client.post(
        "/api/videos/abc/comments/cid1/reply?platform=bitchute", json={"text": "x"}
    )
    assert resp.status_code == 409
    app.state.youtube_service.post_reply.assert_not_called()


def test_reply_comment_mapping():
    comment = {
        "id": "comment1.reply1",
        "snippet": {
            "authorDisplayName": "@someone",
            "textOriginal": "hello",
            "publishedAt": "2026-08-01T12:00:00Z",
            "likeCount": 7,
            "parentId": "comment1",
        },
    }
    reply = _reply_comment(comment)
    assert reply.comment_id == "comment1.reply1"
    assert reply.channel_name == "@someone"
    assert reply.text == "hello"
    assert reply.likes == 7
    # a reply has no replies of its own
    assert reply.replies == 0
    assert reply.timestamp == 1785585600


def test_reply_comment_tolerates_missing_fields():
    reply = _reply_comment({})
    assert reply.comment_id == ""
    assert reply.text == ""
    assert reply.timestamp is None
    assert reply.likes == 0


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


def test_declined_comment_message_is_readable():
    """A 400 processingFailure is YouTube declining the write, not a bad request."""
    declined = _FakeHttpError(
        400, b'{"error":{"errors":[{"reason":"processingFailure","domain":"youtube.comment"}]}}'
    )
    assert _write_message(declined) == (
        "YouTube declined the comment (this thread may not accept new replies)"
    )
    # any other 400 keeps the raw upstream diagnostic
    other = _FakeHttpError(400, b'{"error":{"errors":[{"reason":"commentTextRequired"}]}}')
    assert _write_message(other).startswith("YouTube API error")


# ─── account cookies (personalised home feed) ───

VALID_COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t4102444800\tLOGIN_INFO\tx\n"
    ".youtube.com\tTRUE\t/\tTRUE\t4102444800\tSAPISID\ty\n"
)


def _home(*videos):
    return patch(
        "ytui_server.routers.auth.ytdlp.home_recommendations",
        new=AsyncMock(return_value=list(videos)),
    )


def test_push_cookies_and_status(client, settings):
    with _home(make_video("homeVid00001")):
        assert client.post("/api/auth/youtube/cookies", content=VALID_COOKIES).status_code == 204
    assert client.get("/api/auth/youtube/status").json()["cookies"] is True
    cookie_file = settings.data_dir / "youtube_cookies.txt"
    assert (cookie_file.stat().st_mode & 0o777) == 0o600


def test_push_cookies_rejected_when_youtube_ignores_the_session(client, settings):
    with _home():
        resp = client.post("/api/auth/youtube/cookies", content=VALID_COOKIES)
    assert resp.status_code == 422
    assert "does not recognise" in resp.json()["detail"]
    # unusable cookies must never be kept
    assert not (settings.data_dir / "youtube_cookies.txt").exists()
    assert client.get("/api/auth/youtube/status").json()["cookies"] is False


def test_push_cookies_invalid_payload(client, settings):
    resp = client.post("/api/auth/youtube/cookies", content="not a cookie jar")
    assert resp.status_code == 422
    assert not (settings.data_dir / "youtube_cookies.txt").exists()


def test_delete_cookies_is_idempotent(client):
    with _home(make_video("homeVid00001")):
        client.post("/api/auth/youtube/cookies", content=VALID_COOKIES)
    assert client.delete("/api/auth/youtube/cookies").status_code == 204
    assert client.get("/api/auth/youtube/status").json()["cookies"] is False
    assert client.delete("/api/auth/youtube/cookies").status_code == 204
