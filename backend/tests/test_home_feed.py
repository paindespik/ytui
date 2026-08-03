"""The signed-in YouTube home scrape behind /api/suggestions."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ytui_server.services import ytdlp

FUTURE = int(time.time()) + 365 * 24 * 3600

COOKIES = (
    "# Netscape HTTP Cookie File\n"
    f".youtube.com\tTRUE\t/\tTRUE\t{FUTURE}\tLOGIN_INFO\tx\n"
    f".youtube.com\tTRUE\t/\tTRUE\t{FUTURE}\tSAPISID\ty\n"
)


def _lockup(video_id: str, title: str, channel: str = "A Channel") -> dict:
    return {
        "lockupViewModel": {
            "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "contentId": video_id,
            "metadata": {
                "lockupMetadataViewModel": {
                    "title": {"content": title},
                    "metadata": {
                        "contentMetadataViewModel": {
                            "metadataRows": [
                                {"metadataParts": [{"text": {"content": channel}}]}
                            ]
                        }
                    },
                }
            },
        }
    }


def _page(*items: dict, logged_in: bool = True) -> str:
    data = {"contents": {"richGrid": {"contents": list(items)}}}
    flag = '"LOGGED_IN":true' if logged_in else '"LOGGED_IN":false'
    return (
        f"<html><script>var ytcfg = {{{flag}}};</script>"
        f"<script>var ytInitialData = {json.dumps(data)};</script></html>"
    )


def _serve(html: str, status: int = 200):
    async def fake_get(url, **kwargs):
        return httpx.Response(status, html=html, request=httpx.Request("GET", str(url)))

    return patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=fake_get))


@pytest.fixture
def cookie_file(tmp_path):
    path = tmp_path / "youtube_cookies.txt"
    path.write_text(COOKIES, encoding="utf-8")
    path.chmod(0o600)
    return path


async def test_returns_the_home_videos_in_page_order(cookie_file):
    html = _page(_lockup("vid000000001", "First", "Chan A"), _lockup("vid000000002", "Second"))
    with _serve(html):
        videos = await ytdlp.home_recommendations(cookie_file)
    assert [v.video_id for v in videos] == ["vid000000001", "vid000000002"]
    assert videos[0].channel_title == "Chan A"
    assert videos[0].platform == "youtube"


async def test_limit_is_honoured(cookie_file):
    """The cookie probe in /auth/youtube/cookies asks for a single item."""
    html = _page(*(_lockup(f"vid00000000{i}", f"V{i}") for i in range(1, 4)))
    with _serve(html):
        assert len(await ytdlp.home_recommendations(cookie_file, limit=1)) == 1


async def test_non_video_lockups_are_dropped(cookie_file):
    """Radio playlists and Shorts sit in the same grid but are not watchable videos."""
    mix = {"lockupViewModel": {"contentType": "LOCKUP_CONTENT_TYPE_PLAYLIST", "contentId": "RD1"}}
    with _serve(_page(mix, _lockup("vid000000001", "Real"))):
        videos = await ytdlp.home_recommendations(cookie_file)
    assert [v.video_id for v in videos] == ["vid000000001"]


async def test_logged_out_page_yields_no_videos(cookie_file):
    """An anonymous home must never be served as if it were personalised."""
    html = _page(_lockup("vid000000001", "Anonymous pick"), logged_in=False)
    before = cookie_file.read_text()
    with _serve(html):
        assert await ytdlp.home_recommendations(cookie_file) == []
    # dead cookies are left untouched, not overwritten with a logged-out jar
    assert cookie_file.read_text() == before


async def test_rotated_cookies_are_persisted_privately(cookie_file):
    """Writing the jar back is what keeps the pushed session alive."""
    with _serve(_page(_lockup("vid000000001", "First"))):
        await ytdlp.home_recommendations(cookie_file)
    text = cookie_file.read_text()
    assert "LOGIN_INFO" in text and "SAPISID" in text
    assert (cookie_file.stat().st_mode & 0o777) == 0o600


async def test_missing_initial_data_is_an_upstream_error(cookie_file):
    with _serve('<html>{"LOGGED_IN":true}</html>'):
        with pytest.raises(ytdlp.UpstreamError, match="ytInitialData"):
            await ytdlp.home_recommendations(cookie_file)


async def test_rate_limit_is_reported(cookie_file):
    with _serve(_page(), status=429):
        with pytest.raises(ytdlp.UpstreamError, match="429"):
            await ytdlp.home_recommendations(cookie_file)


async def test_unreadable_cookie_file_is_an_upstream_error(tmp_path):
    junk = tmp_path / "junk.txt"
    junk.write_text("not a cookie jar", encoding="utf-8")
    with pytest.raises(ytdlp.UpstreamError):
        await ytdlp.home_recommendations(junk)
