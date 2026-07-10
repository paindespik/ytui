"""Parsing tests for flat yt-dlp entries (videos, playlists, channels)."""

from ytui.sources.ytdlp_source import entry_to_item


def test_video_entry():
    item = entry_to_item(
        {
            "id": "dQw4w9WgXcQ",
            "title": "A video",
            "channel": "Some Channel",
            "channel_id": "UC123",
            "duration": 212.0,
        }
    )
    assert item is not None
    assert item.kind == "video"
    assert item.duration == 212
    assert item.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert item.thumbnail_url == "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg"


def test_playlist_entry():
    item = entry_to_item(
        {
            "id": "PLabcdef123",
            "title": "A playlist",
            "ie_key": "YoutubeTab",
            "url": "https://www.youtube.com/playlist?list=PLabcdef123",
        }
    )
    assert item is not None
    assert item.kind == "playlist"
    assert item.url == "https://www.youtube.com/playlist?list=PLabcdef123"


def test_channel_entry():
    item = entry_to_item(
        {
            "id": "UCabcdefghij1234567890xx",
            "title": "A channel",
            "ie_key": "YoutubeTab",
            "url": "https://www.youtube.com/@somehandle",
            "thumbnails": [{"url": "https://yt3.ggpht.com/small"}, {"url": "https://yt3.ggpht.com/big"}],
        }
    )
    assert item is not None
    assert item.kind == "channel"
    assert item.url == "https://www.youtube.com/channel/UCabcdefghij1234567890xx"
    assert item.thumbnail_url == "https://yt3.ggpht.com/big"


def test_entry_without_id_is_skipped():
    assert entry_to_item({"title": "broken"}) is None
