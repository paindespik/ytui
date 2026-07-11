"""Live page parsing and notification command handling."""

from ytui.livenotify import parse_live_page

LIVE_PAGE = """
<html><head>
<meta name="title" content="Mon super live &amp; chill">
<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">
</head><body>{"isLive":true}</body></html>
"""

UPCOMING_PAGE = """
<html><head><meta name="title" content="Bientôt">
<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">
</head><body>{"isLive":true,"isUpcoming":true}</body></html>
"""

NO_LIVE_PAGE = """
<html><head>
<link rel="canonical" href="https://www.youtube.com/channel/UCx">
</head><body>nothing live</body></html>
"""


def test_parse_live_page_live():
    video = parse_live_page(LIVE_PAGE, "UCx", "Ma chaîne")
    assert video is not None
    assert video.video_id == "abcdefghijk"
    assert video.title == "Mon super live & chill"
    assert video.channel_id == "UCx"
    assert video.channel_title == "Ma chaîne"
    assert video.kind == "video"


def test_parse_live_page_upcoming_is_not_live():
    assert parse_live_page(UPCOMING_PAGE, "UCx") is None


def test_parse_live_page_no_live():
    assert parse_live_page(NO_LIVE_PAGE, "UCx") is None


def test_parse_live_page_canonical_watch_but_not_live():
    page = LIVE_PAGE.replace('{"isLive":true}', "{}")
    assert parse_live_page(page, "UCx") is None
