"""YouTube auth status, token upload and like/comment with a mocked service."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from ytui_server.services.youtube import ApiError, AuthError

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
    app.state.youtube_service.like_video = MagicMock(
        side_effect=AuthError("No OAuth token on the server.")
    )
    resp = client.post("/api/videos/vid000000001/like")
    assert resp.status_code == 409


def test_like_success(client, app):
    app.state.youtube_service.like_video = MagicMock()
    resp = client.post("/api/videos/vid000000001/like")
    assert resp.status_code == 204
    app.state.youtube_service.like_video.assert_called_once_with("vid000000001")


def test_like_api_error(client, app):
    app.state.youtube_service.like_video = MagicMock(side_effect=ApiError("quota"))
    resp = client.post("/api/videos/vid000000001/like")
    assert resp.status_code == 502


def test_comment_success(client, app):
    app.state.youtube_service.post_comment = MagicMock()
    resp = client.post("/api/videos/vid000000001/comment", json={"text": "Nice!"})
    assert resp.status_code == 204
    app.state.youtube_service.post_comment.assert_called_once_with("vid000000001", "Nice!")


def test_comment_without_token(client, app):
    app.state.youtube_service.post_comment = MagicMock(side_effect=AuthError("no token"))
    resp = client.post("/api/videos/vid000000001/comment", json={"text": "Nice!"})
    assert resp.status_code == 409
