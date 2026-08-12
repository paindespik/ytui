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
    assert request.url.params["source"] == "youtube"


@respx.mock
async def test_channel_videos_omits_empty_query(client):
    respx.get(f"{BASE}/api/channels/UC1/videos").mock(
        return_value=httpx.Response(200, json={"items": [VIDEO_JSON], "has_more": True})
    )
    videos, has_more = await client.channel_videos("UC1", limit=5, offset=10)
    assert len(videos) == 1
    assert has_more is True
    params = respx.calls.last.request.url.params
    assert "q" not in params  # the server rejects q=""
    assert params["limit"] == "5"
    assert params["offset"] == "10"


@respx.mock
async def test_channel_videos_sends_query(client):
    respx.get(f"{BASE}/api/channels/UC1/videos").mock(
        return_value=httpx.Response(200, json={"items": [VIDEO_JSON]})
    )
    videos, has_more = await client.channel_videos("UC1", q="foo", offset=50)
    assert len(videos) == 1
    assert has_more is False
    params = respx.calls.last.request.url.params
    assert params["q"] == "foo"
    assert params["offset"] == "50"
    assert params["platform"] == "youtube"


@respx.mock
async def test_video_streams_sends_max_height(client):
    respx.get(f"{BASE}/api/videos/abc/streams").mock(
        return_value=httpx.Response(200, json={"kind": "progressive", "url": "u", "height": 720})
    )
    streams = await client.video_streams("abc", max_height=720)
    assert streams["height"] == 720
    assert respx.calls.last.request.url.params["max_height"] == "720"


@respx.mock
async def test_search_odysee_source(client):
    odysee_video = dict(
        VIDEO_JSON,
        video_id="ma-video:abc123",
        platform="odysee",
        url="https://odysee.com/ma-video:abc123",
    )
    respx.get(f"{BASE}/api/search").mock(
        return_value=httpx.Response(200, json={"items": [odysee_video]})
    )
    videos = await client.search("query", source="odysee")
    assert respx.calls.last.request.url.params["source"] == "odysee"
    assert videos[0].platform == "odysee"
    assert videos[0].url == "https://odysee.com/ma-video:abc123"


@respx.mock
async def test_video_comments_encodes_video_id(client):
    respx.get(f"{BASE}/api/videos/ma-video%3Aabc123/comments").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "comment_id": "c1",
                        "text": "Great video",
                        "channel_name": "@bob",
                        "timestamp": 1700000000,
                        "replies": 2,
                        "likes": 5,
                        "dislikes": 0,
                        "is_pinned": False,
                    }
                ],
                "total": 1,
                "disabled": False,
            },
        )
    )
    page = await client.video_comments("ma-video:abc123", platform="odysee")
    assert respx.calls.last.request.url.params["platform"] == "odysee"
    assert page.total == 1
    assert page.disabled is False
    assert page.items[0].comment_id == "c1"
    assert page.items[0].channel_name == "@bob"
    assert page.items[0].likes == 5


@respx.mock
async def test_like_video_sends_platform(client):
    route = respx.post(f"{BASE}/api/videos/abc/like").mock(return_value=httpx.Response(204))
    await client.like_video("abc")
    assert route.calls.last.request.url.params["platform"] == "youtube"


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
async def test_timeout_raises_api_error_with_timeout_detail(client):
    respx.get(f"{BASE}/api/videos/abc").mock(side_effect=httpx.ReadTimeout("too slow"))
    with pytest.raises(YtuiApiError) as exc:
        await client.video_details("abc", platform="odysee")
    assert exc.value.status_code == 0
    assert "timed out" in exc.value.detail
    assert "unreachable" not in exc.value.detail


@respx.mock
async def test_video_details_uses_extended_timeout(client):
    from ytui.api_client import SLOW_TIMEOUT

    route = respx.get(f"{BASE}/api/videos/ma-video%3Aabc123").mock(
        return_value=httpx.Response(200, json=VIDEO_JSON | {"description": "d"})
    )
    await client.video_details("ma-video:abc123", platform="odysee")
    request = route.calls.last.request
    assert request.url.params["platform"] == "odysee"
    assert request.extensions["timeout"]["read"] == SLOW_TIMEOUT


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


@respx.mock
async def test_sponsor_segments(client):
    respx.get(f"{BASE}/api/videos/abc/sponsor").mock(
        return_value=httpx.Response(
            200,
            json={
                "segments": [
                    {"category": "sponsor", "start": 10.0, "end": 42.5},
                    {"category": "selfpromo", "start": 60.0, "end": 90.0},
                ]
            },
        )
    )
    segments = await client.sponsor_segments("abc")
    assert [(s.category, s.start, s.end) for s in segments] == [
        ("sponsor", 10.0, 42.5),
        ("selfpromo", 60.0, 90.0),
    ]
    assert respx.calls.last.request.url.params["platform"] == "youtube"


@respx.mock
async def test_push_youtube_cookies_sends_raw_body(client):
    route = respx.post(f"{BASE}/api/auth/youtube/cookies").mock(
        return_value=httpx.Response(204)
    )
    await client.push_youtube_cookies("# Netscape HTTP Cookie File\n")
    request = route.calls.last.request
    assert request.headers["Content-Type"] == "text/plain"
    assert request.content == b"# Netscape HTTP Cookie File\n"


@respx.mock
async def test_delete_youtube_cookies(client):
    route = respx.delete(f"{BASE}/api/auth/youtube/cookies").mock(
        return_value=httpx.Response(204)
    )
    await client.delete_youtube_cookies()
    assert route.called


@respx.mock
async def test_youtube_auth_status_reports_both_credentials(client):
    respx.get(f"{BASE}/api/auth/youtube/status").mock(
        return_value=httpx.Response(200, json={"authenticated": True, "cookies": True})
    )
    assert await client.youtube_auth_status() == (True, True)


@respx.mock
async def test_youtube_auth_status_tolerates_old_server(client):
    # a server without the cookie feature omits the field entirely
    respx.get(f"{BASE}/api/auth/youtube/status").mock(
        return_value=httpx.Response(200, json={"authenticated": True})
    )
    assert await client.youtube_auth_status() == (True, False)


@respx.mock
async def test_import_playlist_into_existing_playlist(client):
    route = respx.post(f"{BASE}/api/playlists/import").mock(
        return_value=httpx.Response(
            201,
            json={
                "playlist": {"id": 3, "name": "Mix", "created_at": 1.0, "count": 12},
                "added": 10,
                "skipped": 2,
                "source_title": "Upstream mix",
            },
        )
    )
    result = await client.import_playlist("PLxyz", target_id=3)
    assert (result.added, result.skipped) == (10, 2)
    assert result.playlist.name == "Mix"
    assert result.source_title == "Upstream mix"
    import json

    body = json.loads(route.calls.last.request.content)
    assert body == {
        "source": "PLxyz",
        "platform": "youtube",
        "name": "",
        "limit": 500,
        "target_id": 3,
    }


@respx.mock
async def test_import_playlist_creates_new_playlist(client):
    route = respx.post(f"{BASE}/api/playlists/import").mock(
        return_value=httpx.Response(
            201,
            json={
                "playlist": {"id": 4, "name": "Upstream", "created_at": 1.0, "count": 3},
                "added": 3,
                "skipped": 0,
                "source_title": "Upstream",
            },
        )
    )
    result = await client.import_playlist(
        "https://www.youtube.com/playlist?list=PLxyz", name="Upstream"
    )
    import json

    assert "target_id" not in json.loads(route.calls.last.request.content)
    assert result.playlist.id == 4


def test_resume_start():
    assert resume_start(50.0, 600.0) == 50.0
    assert resume_start(590.0, 600.0) == 0.0  # within 5% of the end
    assert resume_start(50.0, None) == 0.0
    assert resume_start(50.0, 0.0) == 0.0
