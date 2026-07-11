"""Tests for the OAuth/YouTube write-action module (all mocked, no network)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ytui import auth
from ytui.config import Config


def test_token_path_under_config_dir():
    with patch("ytui.auth.config_dir", return_value=Path("/tmp/xdg/ytui")):
        assert auth.token_path() == Path("/tmp/xdg/ytui/oauth_token.json")


def test_client_secret_path_default_and_override(tmp_path: Path):
    with patch("ytui.auth.config_dir", return_value=tmp_path):
        assert auth.client_secret_path(Config()) == tmp_path / "client_secret.json"
    config = Config.model_validate({"auth": {"client_secret": "~/custom/secret.json"}})
    assert auth.client_secret_path(config) == Path("~/custom/secret.json").expanduser()


def test_like_video_calls_rate():
    youtube = MagicMock()
    auth.like_video(youtube, "abc123")
    youtube.videos.return_value.rate.assert_called_once_with(id="abc123", rating="like")
    youtube.videos.return_value.rate.return_value.execute.assert_called_once_with()


def test_like_video_wraps_errors():
    youtube = MagicMock()
    youtube.videos.return_value.rate.return_value.execute.side_effect = RuntimeError("boom")
    with pytest.raises(auth.ApiError, match="boom"):
        auth.like_video(youtube, "abc123")


def test_post_comment_builds_body():
    youtube = MagicMock()
    auth.post_comment(youtube, "abc123", "nice video")
    youtube.commentThreads.return_value.insert.assert_called_once_with(
        part="snippet",
        body={
            "snippet": {
                "videoId": "abc123",
                "topLevelComment": {"snippet": {"textOriginal": "nice video"}},
            }
        },
    )
    youtube.commentThreads.return_value.insert.return_value.execute.assert_called_once_with()


def test_post_comment_wraps_errors():
    youtube = MagicMock()
    youtube.commentThreads.return_value.insert.return_value.execute.side_effect = RuntimeError(
        "quota"
    )
    with pytest.raises(auth.ApiError, match="quota"):
        auth.post_comment(youtube, "abc123", "hi")


def test_missing_google_deps_gives_install_hint():
    hidden = {
        name: None
        for name in (
            "google",
            "google.auth",
            "google.auth.transport",
            "google.auth.transport.requests",
            "google.oauth2",
            "google.oauth2.credentials",
            "google_auth_oauthlib",
            "google_auth_oauthlib.flow",
            "googleapiclient",
            "googleapiclient.discovery",
        )
    }
    with patch.dict(sys.modules, hidden):
        with pytest.raises(auth.AuthError, match=r"ytui\[auth\]"):
            auth.get_youtube_client(Config())


def test_get_youtube_client_reuses_valid_token(tmp_path: Path):
    creds = MagicMock(valid=True, expired=False)
    credentials_cls = MagicMock()
    credentials_cls.from_authorized_user_file.return_value = creds
    flow_cls = MagicMock()
    build = MagicMock(return_value="client")
    token = tmp_path / "oauth_token.json"
    token.write_text("{}")

    with (
        patch("ytui.auth.token_path", return_value=token),
        patch(
            "ytui.auth._import_google",
            return_value=(MagicMock(), credentials_cls, flow_cls, build),
        ),
    ):
        client = auth.get_youtube_client(Config())

    assert client == "client"
    credentials_cls.from_authorized_user_file.assert_called_once_with(str(token), auth.SCOPES)
    flow_cls.from_client_secrets_file.assert_not_called()
    build.assert_called_once_with("youtube", "v3", credentials=creds)


def test_get_youtube_client_missing_secret(tmp_path: Path):
    credentials_cls = MagicMock()
    with (
        patch("ytui.auth.token_path", return_value=tmp_path / "oauth_token.json"),
        patch("ytui.auth.config_dir", return_value=tmp_path),
        patch(
            "ytui.auth._import_google",
            return_value=(MagicMock(), credentials_cls, MagicMock(), MagicMock()),
        ),
    ):
        with pytest.raises(auth.AuthError, match="client_secret"):
            auth.get_youtube_client(Config())


def test_get_youtube_client_runs_flow_and_saves_token(tmp_path: Path):
    creds = MagicMock()
    creds.to_json.return_value = '{"token": "t"}'
    flow_cls = MagicMock()
    flow_cls.from_client_secrets_file.return_value.run_local_server.return_value = creds
    build = MagicMock(return_value="client")
    secret = tmp_path / "client_secret.json"
    secret.write_text("{}")
    token = tmp_path / "oauth_token.json"

    with (
        patch("ytui.auth.token_path", return_value=token),
        patch("ytui.auth.config_dir", return_value=tmp_path),
        patch(
            "ytui.auth._import_google",
            return_value=(MagicMock(), MagicMock(), flow_cls, build),
        ),
    ):
        client = auth.get_youtube_client(Config())

    assert client == "client"
    flow_cls.from_client_secrets_file.assert_called_once_with(str(secret), auth.SCOPES)
    assert token.read_text() == '{"token": "t"}'
    assert (token.stat().st_mode & 0o777) == 0o600
