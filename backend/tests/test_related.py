"""/api/videos/{id}/related for non-YouTube platforms: same-channel fallback."""

from __future__ import annotations

from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest

from ytui_server.models import Video, VideoDetails
from ytui_server.services import crowdbunker, odysee, ytdlp

CHAN = "@channel:abcdef0123456789abcdef0123456789"


def _details(channel_id: str) -> VideoDetails:
    return VideoDetails(video_id="cur0001", title="Current", channel_id=channel_id)


def _videos(
    ids: list[str],
    platform: Literal["youtube", "bitchute", "odysee", "twitch", "tiktok", "crowdbunker"],
) -> list[Video]:
    return [Video(video_id=i, title=i, platform=platform) for i in ids]


def test_related_odysee_lists_channel_and_excludes_current(client):
    video_id = "name:abcdef0123456789abcdef0123456789"
    with (
        patch.object(
            ytdlp, "video_details", new=AsyncMock(return_value=_details(CHAN))
        ),
        patch.object(
            odysee,
            "channel_videos",
            new=AsyncMock(
                return_value=_videos([video_id, "a1", "a2"], "odysee")
            ),
        ) as listing,
    ):
        resp = client.get(f"/api/videos/{video_id}/related", params={"platform": "odysee"})
    assert resp.status_code == 200
    assert [v["video_id"] for v in resp.json()["items"]] == ["a1", "a2"]
    listing.assert_awaited_once_with(CHAN, limit=20)


def test_related_bitchute_lists_channel(client):
    with (
        patch.object(
            ytdlp, "video_details", new=AsyncMock(return_value=_details("BUchan123"))
        ),
        patch.object(
            ytdlp,
            "channel_videos",
            new=AsyncMock(return_value=_videos(["cur0001", "b1"], "bitchute")),
        ) as listing,
    ):
        resp = client.get(
            "/api/videos/cur0001/related", params={"platform": "bitchute"}
        )
    assert resp.status_code == 200
    assert [v["video_id"] for v in resp.json()["items"]] == ["b1"]
    listing.assert_awaited_once_with(
        "https://www.bitchute.com/channel/BUchan123/", limit=20
    )


def test_related_twitch_lists_channel(client):
    with (
        patch.object(
            ytdlp, "video_details", new=AsyncMock(return_value=_details("someninja"))
        ),
        patch.object(
            ytdlp,
            "channel_videos",
            new=AsyncMock(return_value=_videos(["v123456", "cur0001"], "twitch")),
        ),
    ):
        resp = client.get(
            "/api/videos/v123456/related", params={"platform": "twitch"}
        )
    assert resp.status_code == 200
    assert [v["video_id"] for v in resp.json()["items"]] == ["cur0001"]


def test_related_crowdbunker_lists_channel(client):
    with (
        patch.object(
            ytdlp, "video_details", new=AsyncMock(return_value=_details("OrgUid"))
        ),
        patch.object(
            crowdbunker,
            "channel_videos",
            new=AsyncMock(
                return_value=_videos(["cur0001", "cb1", "cb2"], "crowdbunker")
            ),
        ) as listing,
    ):
        resp = client.get(
            "/api/videos/cur0001/related", params={"platform": "crowdbunker"}
        )
    assert resp.status_code == 200
    assert [v["video_id"] for v in resp.json()["items"]] == ["cb1", "cb2"]
    listing.assert_awaited_once_with("OrgUid", limit=20)


def test_related_respects_limit(client):
    with (
        patch.object(
            ytdlp, "video_details", new=AsyncMock(return_value=_details("OrgUid"))
        ),
        patch.object(
            crowdbunker,
            "channel_videos",
            new=AsyncMock(
                return_value=_videos(["cur0001", "cb1", "cb2", "cb3"], "crowdbunker")
            ),
        ),
    ):
        resp = client.get(
            "/api/videos/cur0001/related",
            params={"platform": "crowdbunker", "limit": 2},
        )
    assert resp.status_code == 200
    assert [v["video_id"] for v in resp.json()["items"]] == ["cb1", "cb2"]


def test_related_empty_channel_id_returns_empty(client):
    with patch.object(
        ytdlp, "video_details", new=AsyncMock(return_value=_details(""))
    ):
        resp = client.get(
            "/api/videos/name:abcdef0123456789abcdef0123456789/related",
            params={"platform": "odysee"},
        )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


@pytest.mark.parametrize("platform", ["odysee", "bitchute", "twitch", "crowdbunker"])
def test_related_details_failure_is_502(client, platform):
    with patch.object(
        ytdlp,
        "video_details",
        new=AsyncMock(side_effect=ytdlp.UpstreamError("boom")),
    ):
        resp = client.get("/api/videos/cur0001/related", params={"platform": platform})
    assert resp.status_code == 502


def test_related_listing_failure_is_502(client):
    with (
        patch.object(
            ytdlp, "video_details", new=AsyncMock(return_value=_details("OrgUid"))
        ),
        patch.object(
            crowdbunker,
            "channel_videos",
            new=AsyncMock(side_effect=crowdbunker.CrowdBunkerError("boom")),
        ),
    ):
        resp = client.get(
            "/api/videos/cur0001/related", params={"platform": "crowdbunker"}
        )
    assert resp.status_code == 502
