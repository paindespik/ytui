"""Platform-aware URL/RSS resolution tests for Video and Channel models."""

from ytui.models import Channel, Video, video_id_from_url


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


def test_video_url_odysee():
    v = Video(video_id="ma-video:abc123", title="v", platform="odysee")
    assert v.url == "https://odysee.com/ma-video:abc123"


def test_video_url_odysee_channel():
    v = Video(video_id="@chan:1", title="c", kind="channel", platform="odysee")
    assert v.url == "https://odysee.com/@chan:1"


def test_video_url_odysee_playlist():
    v = Video(video_id="pl123", title="p", kind="playlist", platform="odysee")
    assert v.url == "https://odysee.com/$/playlist/pl123"


def test_video_url_twitch_live():
    """Live composite id "login:stream_id" routes to the channel page."""
    v = Video(video_id="zerator:123456789", title="L", platform="twitch")
    assert v.url == "https://www.twitch.tv/zerator"


def test_video_url_twitch_vod():
    """VOD v-prefixed id routes to the videos page."""
    v = Video(video_id="v246974233", title="V", platform="twitch")
    assert v.url == "https://www.twitch.tv/videos/246974233"


def test_video_url_twitch_channel():
    v = Video(video_id="zerator", title="Z", kind="channel", platform="twitch")
    assert v.url == "https://www.twitch.tv/zerator"


def test_video_url_tiktok_live():
    """Live composite id "username:room_id" routes to the /live page."""
    v = Video(video_id="weathernewslive:755123", title="L", platform="tiktok")
    assert v.url == "https://www.tiktok.com/@weathernewslive/live"


def test_video_url_tiktok_channel():
    v = Video(video_id="weathernewslive", title="W", kind="channel", platform="tiktok")
    assert v.url == "https://www.tiktok.com/@weathernewslive"


def test_video_url_tiktok_video():
    v = Video(video_id="7300000000", title="V", channel_id="user", platform="tiktok")
    assert v.url == "https://www.tiktok.com/@user/video/7300000000"
    bare = Video(video_id="7300000000", title="V", platform="tiktok")
    assert bare.url == "https://www.tiktok.com/embed/7300000000"


def test_video_platform_defaults_to_youtube():
    assert Video(video_id="abc123", title="v").platform == "youtube"


# -- video_id_from_url --


def test_video_id_from_url():
    assert video_id_from_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == (
        "dQw4w9WgXcQ",
        "youtube",
    )
    assert video_id_from_url("https://youtu.be/dQw4w9WgXcQ") == ("dQw4w9WgXcQ", "youtube")
    assert video_id_from_url("https://www.bitchute.com/video/AbC123xyz/") == (
        "AbC123xyz",
        "bitchute",
    )
    assert video_id_from_url("https://www.youtube.com/playlist?list=PL1") is None


def test_video_id_from_url_other_platforms():
    assert video_id_from_url("https://crowdbunker.com/v/0z4Kms8pi8I") == (
        "0z4Kms8pi8I",
        "crowdbunker",
    )
    # Twitch VODs map to the 'v{id}' convention used by Video.url.
    assert video_id_from_url("https://www.twitch.tv/videos/123456789") == (
        "v123456789",
        "twitch",
    )
    assert video_id_from_url("https://www.tiktok.com/@someuser/video/7345678901234567890") == (
        "7345678901234567890",
        "tiktok",
    )
    # live/channel pages are not video URLs
    assert video_id_from_url("https://www.twitch.tv/somechannel") is None
    assert video_id_from_url("https://www.tiktok.com/@someuser/live") is None


def test_video_id_from_url_odysee():
    assert video_id_from_url("https://odysee.com/ma-video:abc123") == (
        "ma-video:abc123",
        "odysee",
    )
    assert video_id_from_url("https://odysee.com/@chan:1/ma-video:abc123") == (
        "ma-video:abc123",
        "odysee",
    )
    # channel pages and arbitrary paths are not video URLs
    assert video_id_from_url("https://odysee.com/@chan:1") is None
    assert video_id_from_url("https://odysee.com/foo") is None


# -- Channel.rss_url --


def test_channel_rss_url_youtube():
    c = Channel(channel_id="UCtest000000000000000000")
    assert c.rss_url == (
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest000000000000000000"
    )


def test_channel_rss_url_bitchute():
    c = Channel(channel_id="bcsupport", platform="bitchute")
    assert c.rss_url == "https://api.bitchute.com/feeds/rss/channel/bcsupport"


def test_channel_rss_url_odysee():
    c = Channel(channel_id="@chan:1", platform="odysee")
    assert c.rss_url == "https://odysee.com/$/rss/@chan:1"


def test_channel_platform_defaults_to_youtube():
    assert Channel(channel_id="UCtest").platform == "youtube"
