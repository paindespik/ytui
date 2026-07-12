"""YtuiClient against a mocked httpx transport (respx)."""

import httpx
import pytest
import respx

from ytui.api_client import YtuiApiError, YtuiClient, resume_start

BASE = "https://ytui.example.com"

VIDEO_JSON = {
    "video_id": "dQw4w9WgXcQ",
    "title": "A video",
    "channel_title": "Some Channel",
    "channel_id": "UC123",
    "published": "2024-01-01T00:00:00Z",
    "duration": 212,
    "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
    "kind": "video",
    "platform": "youtube",
    "playlist_id": "",
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
}


@pytest.fixture
def client():
    return YtuiClient(BASE, "secret-token")


@respx.mock
async def test_token_sent_in_authorization_header(client):
    route = respx.get(f"{BASE}/api/feed").mock(
        return_value=httpx.Response(200, json={"videos": [], "warnings": []})
    )
    await client.feed()
    assert route.calls.last.request.headers["Authorization"] == "Bearer secret-token"


@respx.mock
async def test_feed_parses_videos_and_warnings(client):
    respx.get(f"{BASE}/api/feed").mock(
        return_value=httpx.Response(
            200, json={"videos": [VIDEO_JSON], "warnings": ["channel X offline"]}
        )
    )
    result = await client.feed(refresh=True)
    assert result.videos[0].video_id == "dQw4w9WgXcQ"
    assert result.videos[0].url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert result.warnings == ["channel X offline"]
    assert respx.calls.last.request.url.params["refresh"] == "true"


@respx.mock
async def test_search(client):
    respx.get(f"{BASE}/api/search").mock(
        return_value=httpx.Response(200, json={"items": [VIDEO_JSON]})
    )
    videos = await client.search("query", limit=5)
    assert len(videos) == 1
    request = respx.calls.last.request
    assert request.url.params["q"] == "query"
    assert request.url.params["limit"] == "5"


@respx.mock
async def test_http_error_raises_api_error_with_detail(client):
    respx.get(f"{BASE}/api/search").mock(
        return_value=httpx.Response(502, json={"detail": "Search failed: boom"})
    )
    with pytest.raises(YtuiApiError) as exc:
        await client.search("query")
    assert exc.value.status_code == 502
    assert exc.value.detail == "Search failed: boom"


@respx.mock
async def test_connection_error_raises_api_error_status_zero(client):
    respx.get(f"{BASE}/api/feed").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(YtuiApiError) as exc:
        await client.feed()
    assert exc.value.status_code == 0
    assert "unreachable" in exc.value.detail


@respx.mock
async def test_record_watch_serializes_video(client):
    from ytui.models import Video

    route = respx.post(f"{BASE}/api/history").mock(return_value=httpx.Response(204))
    await client.record_watch(Video(video_id="abc123def45", title="T"))
    body = route.calls.last.request.content
    assert b'"video_id":"abc123def45"' in body.replace(b" ", b"")


@respx.mock
async def test_watched_ids_returns_set(client):
    respx.get(f"{BASE}/api/history/watched-ids").mock(
        return_value=httpx.Response(200, json={"ids": ["a", "b", "a"]})
    )
    assert await client.watched_ids() == {"a", "b"}


@respx.mock
async def test_resume_none_on_404(client):
    respx.get(f"{BASE}/api/history/xyz/resume").mock(
        return_value=httpx.Response(404, json={"detail": "Video not in history"})
    )
    assert await client.resume("xyz") is None


@respx.mock
async def test_resume_returns_tuple(client):
    respx.get(f"{BASE}/api/history/xyz/resume").mock(
        return_value=httpx.Response(
            200, json={"position": 42.5, "duration": 600.0, "playlist_id": "PL1"}
        )
    )
    assert await client.resume("xyz") == (42.5, 600.0, "PL1")


@respx.mock
async def test_save_position(client):
    route = respx.put(f"{BASE}/api/history/xyz/position").mock(
        return_value=httpx.Response(204)
    )
    await client.save_position("xyz", 12.0, 60.0)
    assert route.called


@respx.mock
async def test_create_playlist_returns_none_on_conflict(client):
    respx.post(f"{BASE}/api/playlists").mock(
        return_value=httpx.Response(409, json={"detail": "Playlist name already taken"})
    )
    assert await client.create_playlist("mix") is None


@respx.mock
async def test_create_playlist_returns_id(client):
    respx.post(f"{BASE}/api/playlists").mock(
        return_value=httpx.Response(
            201, json={"id": 7, "name": "mix", "created_at": 1.0, "count": 0}
        )
    )
    assert await client.create_playlist("mix") == 7


@respx.mock
async def test_add_playlist_item_false_on_conflict(client):
    from ytui.models import Video

    respx.post(f"{BASE}/api/playlists/1/items").mock(
        return_value=httpx.Response(409, json={"detail": "Item already in playlist"})
    )
    assert await client.add_playlist_item(1, Video(video_id="v", title="t")) is False


@respx.mock
async def test_playlist_items(client):
    respx.get(f"{BASE}/api/playlists/1/items").mock(
        return_value=httpx.Response(200, json=[{"position": 0, "video": VIDEO_JSON}])
    )
    items = await client.playlist_items(1)
    assert items[0].position == 0
    assert items[0].video.video_id == "dQw4w9WgXcQ"


@respx.mock
async def test_follow_channel_conflict(client):
    respx.post(f"{BASE}/api/channels").mock(
        return_value=httpx.Response(409, json={"detail": "Channel already followed"})
    )
    with pytest.raises(YtuiApiError) as exc:
        await client.follow_channel("@handle")
    assert exc.value.status_code == 409


@respx.mock
async def test_channels_parsed(client):
    respx.get(f"{BASE}/api/channels").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"ref": "@x", "channel_id": "UC1", "title": "X", "platform": "youtube"},
            ],
        )
    )
    channels = await client.channels()
    assert channels[0].ref == "@x"
    assert channels[0].channel_id == "UC1"


@respx.mock
async def test_lives(client):
    respx.get(f"{BASE}/api/lives").mock(
        return_value=httpx.Response(
            200, json=[{"video": VIDEO_JSON, "detected_at": "2024-01-01T00:00:00Z"}]
        )
    )
    lives = await client.lives()
    assert lives[0].video_id == "dQw4w9WgXcQ"


@respx.mock
async def test_video_details(client):
    respx.get(f"{BASE}/api/videos/abc").mock(
        return_value=httpx.Response(
            200,
            json={
                "video_id": "abc",
                "title": "T",
                "description": "D",
                "view_count": 10,
                "duration": 60,
            },
        )
    )
    details = await client.video_details("abc")
    assert details.description == "D"
    assert details.view_count == 10


@respx.mock
async def test_push_youtube_token_and_status(client):
    push = respx.post(f"{BASE}/api/auth/youtube/token").mock(
        return_value=httpx.Response(204)
    )
    respx.get(f"{BASE}/api/auth/youtube/status").mock(
        return_value=httpx.Response(200, json={"authenticated": True})
    )
    await client.push_youtube_token('{"token": "x"}')
    assert push.calls.last.request.content == b'{"token": "x"}'
    assert await client.auth_status() is True


def test_resume_start():
    assert resume_start(50.0, 600.0) == 50.0
    assert resume_start(590.0, 600.0) == 0.0  # within 5% of the end
    assert resume_start(50.0, None) == 0.0
    assert resume_start(50.0, 0.0) == 0.0
