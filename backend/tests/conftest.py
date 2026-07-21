"""Shared test fixtures: app with tmp data dir, authenticated client."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ytui_server.main import create_app
from ytui_server.models import Video
from ytui_server.services import ytdlp
from ytui_server.settings import Settings

TEST_TOKEN = "test-token-123"

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clear_info_cache():
    """The yt-dlp extract_info TTL cache must not leak between tests."""
    ytdlp._INFO_CACHE.clear()
    yield
    ytdlp._INFO_CACHE.clear()


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(YTUI_API_TOKEN=TEST_TOKEN, YTUI_DATA_DIR=tmp_path / "data")


@pytest.fixture()
def app(settings):
    return create_app(settings, start_live_poll=False)


@pytest.fixture()
def client(app):
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        yield client


@pytest.fixture()
def anon_client(app):
    with TestClient(app) as client:
        yield client


def make_video(video_id: str = "abc123def45", **kwargs) -> Video:
    defaults = dict(
        video_id=video_id,
        title=f"Video {video_id}",
        channel_title="Test Channel",
        channel_id="UCtest000000000000000000",
    )
    defaults.update(kwargs)
    return Video(**defaults)
