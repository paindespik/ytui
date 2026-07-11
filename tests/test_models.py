"""Platform-aware URL/RSS resolution tests for Video and Channel models."""

from ytui.models import Channel, Video


# -- Video.url --


def test_video_url_youtube():
    assert Video(video_id="abc123", title="v").url == "https://www.youtube.com/watch?v=abc123"


def test_video_url_youtube_playlist():
    v = Video(video_id="PL123", title="p", kind="playlist")
    assert v.url == "https://www.youtube.com/playlist?list=PL123"


def test_video_url_youtube_channel():
    v = Video(video_id="UC123", title="c", kind="channel")
    assert v.url == "https://www.youtube.com/channel/UC123"


def test_video_url_bitchute():
    v = Video(video_id="abc123", title="v", platform="bitchute")
    assert v.url == "https://www.bitchute.com/video/abc123/"


def test_video_url_bitchute_channel():
    v = Video(video_id="bcsupport", title="c", kind="channel", platform="bitchute")
    assert v.url == "https://www.bitchute.com/channel/bcsupport/"


def test_video_url_bitchute_playlist():
    v = Video(video_id="pl123", title="p", kind="playlist", platform="bitchute")
    assert v.url == "https://www.bitchute.com/playlist/pl123/"


def test_video_platform_defaults_to_youtube():
    assert Video(video_id="abc123", title="v").platform == "youtube"


# -- Channel.rss_url --


def test_channel_rss_url_youtube():
    c = Channel(channel_id="UCtest000000000000000000")
    assert c.rss_url == (
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest000000000000000000"
    )


def test_channel_rss_url_bitchute():
    c = Channel(channel_id="bcsupport", platform="bitchute")
    assert c.rss_url == "https://api.bitchute.com/feeds/rss/channel/bcsupport"


def test_channel_platform_defaults_to_youtube():
    assert Channel(channel_id="UCtest").platform == "youtube"
