"""YouTube Music home (/api/music) and signed-in playlist listings."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ytui_server.models import MusicShelf, Video
from ytui_server.services import music, ytdlp

FUTURE = int(time.time()) + 365 * 24 * 3600

COOKIES = (
    "# Netscape HTTP Cookie File\n"
    f".youtube.com\tTRUE\t/\tTRUE\t{FUTURE}\tLOGIN_INFO\tx\n"
    f".youtube.com\tTRUE\t/\tTRUE\t{FUTURE}\tSAPISID\ty\n"
)

ARTIST_ID = "UCartist0000000000000000"


# ─── InnerTube builders (shapes measured on music.youtube.com, data synthetic) ───


def _runs(*parts: str | tuple[str, str, str]) -> dict:
    """Text runs; a (text, browse_id, page_type) tuple makes a linked run."""
    runs = []
    for part in parts:
        if isinstance(part, tuple):
            text, browse_id, page = part
            runs.append({
                "text": text,
                "navigationEndpoint": {"browseEndpoint": {
                    "browseId": browse_id,
                    "browseEndpointContextSupportedConfigs": {
                        "browseEndpointContextMusicConfig": {"pageType": page}
                    },
                }},
            })
        else:
            runs.append({"text": part})
    return {"runs": runs}


def _thumb(url: str, width: int = 120) -> dict:
    return {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
        {"url": url, "width": width, "height": width}
    ]}}}


def _browse_nav(browse_id: str, page: str) -> dict:
    return {"browseEndpoint": {
        "browseId": browse_id,
        "browseEndpointContextSupportedConfigs": {
            "browseEndpointContextMusicConfig": {"pageType": page}
        },
    }}


def _track(video_id: str, title: str, subtitle: dict, fixed: str = "") -> dict:
    """A 'Sélection rapide'-style list row."""
    renderer = {
        "thumbnail": _thumb(f"https://yt3.googleusercontent.com/{video_id}=w60-h60-l90-rj", 60),
        "flexColumns": [
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{
                "text": title,
                "navigationEndpoint": {"watchEndpoint": {"videoId": video_id}},
            }]}}},
            {"musicResponsiveListItemFlexColumnRenderer": {"text": subtitle}},
        ],
        "playlistItemData": {"videoId": video_id},
    }
    if fixed:
        renderer["fixedColumns"] = [
            {"musicResponsiveListItemFixedColumnRenderer": {"text": _runs(fixed)}}
        ]
    return {"musicResponsiveListItemRenderer": renderer}


def _card(title: str, subtitle: dict, nav: dict, overlay_playlist: str = "") -> dict:
    renderer = {
        "thumbnailRenderer": _thumb("https://i.ytimg.com/vi/x/hq720.jpg", 800),
        "title": _runs(title),
        "subtitle": subtitle,
        "navigationEndpoint": nav,
    }
    if overlay_playlist:
        renderer["thumbnailOverlay"] = {"musicItemThumbnailOverlayRenderer": {"content": {
            "musicPlayButtonRenderer": {"playNavigationEndpoint": {
                "watchPlaylistEndpoint": {"playlistId": overlay_playlist}
            }}
        }}}
    return {"musicTwoRowItemRenderer": renderer}


def _shelf(title: str, *items: dict, strapline: str = "") -> dict:
    header = {"title": _runs(title)}
    if strapline:
        header["strapline"] = _runs(strapline)
    return {"musicCarouselShelfRenderer": {
        "header": {"musicCarouselShelfBasicHeaderRenderer": header},
        "contents": list(items),
    }}


def _home_page(*shelves: dict, continuation: str = "") -> dict:
    section: dict = {"contents": list(shelves)}
    if continuation:
        section["continuations"] = [{"nextContinuationData": {"continuation": continuation}}]
    return {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [
        {"tabRenderer": {"content": {"sectionListRenderer": section}}}
    ]}}}


def _home_continuation(*shelves: dict) -> dict:
    return {"continuationContents": {"sectionListContinuation": {"contents": list(shelves)}}}


def _music_html(logged_in: bool = True) -> str:
    context = {"client": {"clientName": "WEB_REMIX", "clientVersion": "1.2026", "hl": "fr"}}
    flag = '"LOGGED_IN":true' if logged_in else '"LOGGED_IN":false'
    return f'<html><script>ytcfg.set({{{flag},"INNERTUBE_CONTEXT":{json.dumps(context)}}});</script></html>'


QUICK_PICKS = _shelf(
    "Sélection rapide",
    _track("trackAAAAAA", "Song A", _runs(("Artist A", ARTIST_ID, "MUSIC_PAGE_TYPE_ARTIST"),
                                           " • ", "222\xa0M\xa0lectures")),
    _track("trackBBBBBB", "Long mix", _runs("Uploader", " • ", "1:11:26"), fixed="1:11:26"),
)
ALBUMS = _shelf(
    "Albums pour vous",
    _card(
        "An Album",
        _runs("Album", " • ", ("Artist A", ARTIST_ID, "MUSIC_PAGE_TYPE_ARTIST")),
        _browse_nav("MPREb_album", "MUSIC_PAGE_TYPE_ALBUM"),
        overlay_playlist="OLAK5uy_album",
    ),
    # An album card without its audio playlist cannot be listed: dropped.
    _card("Ghost", _runs("Album"), _browse_nav("MPREb_ghost", "MUSIC_PAGE_TYPE_ALBUM")),
)
MIXES = _shelf(
    "Mixés pour vous",
    _card("Mon supermix", _runs("A, B et C"), _browse_nav("VLRDTMAK5uy_mix", "MUSIC_PAGE_TYPE_PLAYLIST")),
    _card("Podcast", _runs("Show"), _browse_nav("MPSPshow", "MUSIC_PAGE_TYPE_PODCAST_SHOW_DETAIL_PAGE")),
)
RELATED = _shelf(
    "Artist A",
    _card("Artist B", _runs("209\xa0k\xa0abonnés"), _browse_nav("UCartistB00000000000000", "MUSIC_PAGE_TYPE_ARTIST")),
    _card("Clip", _runs("Artist B", " • ", "2,7\xa0Md de vues"), {"watchEndpoint": {"videoId": "clipCCCCCCC"}}),
    strapline="EN RAPPORT AVEC",
)


# ─── parsing ───


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("222\xa0M\xa0lectures", 222_000_000),
        ("2,7 Md de vues", 2_700_000_000),
        ("57 k lectures", 57_000),
        ("1 M lectures", 1_000_000),
        ("1.2M views", 1_200_000),
        ("1\u202f234 vues", 1_234),
        ("209 k abonnés", None),
        ("Album", None),
        ("1:11:26", None),
    ],
)
def test_parse_count(text, expected):
    assert music._parse_count(text) == expected


def test_parse_shelves_maps_every_kind():
    shelves = music._parse_shelves(_home_page(QUICK_PICKS, ALBUMS, MIXES, RELATED))

    assert [s.title for s in shelves] == [
        "Sélection rapide", "Albums pour vous", "Mixés pour vous", "Artist A",
    ]
    track, long_mix = shelves[0].items
    assert (track.kind, track.video_id, track.title) == ("video", "trackAAAAAA", "Song A")
    assert track.channel_title == "Artist A"
    assert track.channel_id == ARTIST_ID
    assert track.view_count == 222_000_000
    # Square cover art is re-requested big enough for tiles.
    assert "=w544-h544" in track.thumbnail_url
    assert (long_mix.channel_title, long_mix.duration) == ("Uploader", 4286)

    (album,) = shelves[1].items
    assert (album.kind, album.video_id) == ("playlist", "OLAK5uy_album")
    assert album.channel_title == "Album • Artist A"
    assert album.channel_id == ARTIST_ID

    (mix,) = shelves[2].items  # the podcast is skipped
    assert (mix.kind, mix.video_id, mix.channel_title) == ("playlist", "RDTMAK5uy_mix", "A, B et C")

    artist, clip = shelves[3].items
    assert shelves[3].strapline == "EN RAPPORT AVEC"
    assert (artist.kind, artist.video_id, artist.channel_id) == (
        "channel", "UCartistB00000000000000", "UCartistB00000000000000"
    )
    assert (clip.kind, clip.video_id, clip.view_count) == ("video", "clipCCCCCCC", 2_700_000_000)


def test_parse_shelves_drops_empty_shelves_and_duplicates():
    dup = _track("trackAAAAAA", "Song A", _runs("Artist A"))
    page = _home_page(
        _shelf("Nothing playable", _card("Podcast", _runs("x"), _browse_nav("MPSP", "MUSIC_PAGE_TYPE_PODCAST_SHOW_DETAIL_PAGE"))),
        _shelf("Twice", dup, dup),
    )
    shelves = music._parse_shelves(page)
    assert [(s.title, len(s.items)) for s in shelves] == [("Twice", 1)]


# ─── InnerTube session ───


def _serve(*, html: str, browse: list[dict], calls: list | None = None):
    """Patch the GET of music.youtube.com and the successive browse POSTs."""
    pages = iter(browse)

    async def fake_get(url, **kwargs):
        return httpx.Response(200, html=html, request=httpx.Request("GET", str(url)))

    async def fake_post(url, **kwargs):
        if calls is not None:
            calls.append((str(url), kwargs))
        return httpx.Response(200, json=next(pages), request=httpx.Request("POST", str(url)))

    return (
        patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=fake_get)),
        patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=fake_post)),
    )


@pytest.fixture()
def cookie_file(tmp_path):
    path = tmp_path / "youtube_cookies.txt"
    path.write_text(COOKIES)
    return path


async def test_home_follows_continuations_signed_in(cookie_file):
    calls: list = []
    get_patch, post_patch = _serve(
        html=_music_html(),
        browse=[_home_page(QUICK_PICKS, continuation="tok1"), _home_continuation(ALBUMS)],
        calls=calls,
    )
    with get_patch, post_patch:
        shelves = await music.home(cookie_file)

    assert shelves is not None
    assert [s.title for s in shelves] == ["Sélection rapide", "Albums pour vous"]
    first_url, first = calls[0]
    assert first["json"]["browseId"] == "FEmusic_home"
    assert first["json"]["context"]["client"]["clientName"] == "WEB_REMIX"
    assert first["headers"]["Authorization"].startswith("SAPISIDHASH ")
    assert first["headers"]["Origin"] == "https://music.youtube.com"
    assert calls[1][1]["params"] == {"ctoken": "tok1", "continuation": "tok1", "type": "next"}
    # The jar is read, never rewritten: the server-side browser owns it.
    assert cookie_file.read_text() == COOKIES


async def test_home_signed_out_returns_none(cookie_file):
    get_patch, post_patch = _serve(html=_music_html(logged_in=False), browse=[])
    with get_patch, post_patch:
        assert await music.home(cookie_file) is None


async def test_playlist_videos_pages_through_continuations(cookie_file):
    first = {
        "header": {"musicResponsiveHeaderRenderer": {"title": _runs('Musique "J\'aime"')}},
        "contents": {"twoColumnBrowseResultsRenderer": {"secondaryContents": {"sectionListRenderer": {
            "contents": [{"musicPlaylistShelfRenderer": {"contents": [
                _track("likedAAAAAA", "Liked A", _runs("Artist A"), fixed="4:22"),
                {"continuationItemRenderer": {"continuationEndpoint": {
                    "continuationCommand": {"token": "next-page"}
                }}},
            ]}}],
        }}}},
    }
    second = {"onResponseReceivedActions": [{"appendContinuationItemsAction": {
        "continuationItems": [_track("likedBBBBBB", "Liked B", _runs("Artist B"))]
    }}]}
    calls: list = []
    get_patch, post_patch = _serve(html=_music_html(), browse=[first, second], calls=calls)
    with get_patch, post_patch:
        listed = await music.playlist_videos(cookie_file, "LM")

    assert listed is not None
    videos, title = listed

    assert title == 'Musique "J\'aime"'
    assert [(v.video_id, v.duration) for v in videos] == [("likedAAAAAA", 262), ("likedBBBBBB", None)]
    assert calls[0][1]["json"]["browseId"] == "VLLM"
    assert calls[1][1]["json"]["continuation"] == "next-page"


# ─── API ───


SHELVES = [MusicShelf(title="Sélection rapide", items=[
    Video(video_id="trackAAAAAA", title="Song A", channel_title="Artist A"),
])]


def _push_cookies(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "youtube_cookies.txt").write_text(COOKIES)


def test_music_without_cookies_warns(client):
    resp = client.get("/api/music")
    assert resp.status_code == 200
    assert resp.json() == {"shelves": [], "warnings": [music.NO_COOKIES_WARNING]}


def test_music_serves_and_caches_shelves(client, settings):
    _push_cookies(settings)
    with patch.object(music, "home", AsyncMock(return_value=SHELVES)) as home:
        first = client.get("/api/music").json()
        second = client.get("/api/music").json()
        client.get("/api/music", params={"refresh": True})

    assert first == second
    assert first["warnings"] == []
    assert first["shelves"][0]["title"] == "Sélection rapide"
    item = first["shelves"][0]["items"][0]
    assert item["url"] == "https://www.youtube.com/watch?v=trackAAAAAA"
    assert home.await_count == 2  # cached once, then forced


def test_music_dead_session_warns(client, settings):
    _push_cookies(settings)
    with patch.object(music, "home", AsyncMock(return_value=None)):
        body = client.get("/api/music").json()
    assert body == {"shelves": [], "warnings": [music.STALE_COOKIES_WARNING]}


def test_music_upstream_error_keeps_the_last_shelves(client, settings):
    _push_cookies(settings)
    with patch.object(music, "home", AsyncMock(return_value=SHELVES)):
        client.get("/api/music")
    with patch.object(music, "home", AsyncMock(side_effect=ytdlp.UpstreamError("boom"))):
        body = client.get("/api/music", params={"refresh": True}).json()
    assert body["shelves"][0]["title"] == "Sélection rapide"
    assert body["warnings"] == ["YouTube Music: boom"]


def test_personal_mix_is_listed_signed_in(client, settings):
    _push_cookies(settings)
    liked = [Video(video_id="likedAAAAAA", title="Liked A")]
    with (
        patch.object(music, "playlist_videos", AsyncMock(return_value=(liked, "Mon supermix"))) as signed,
        patch.object(ytdlp, "playlist_videos", AsyncMock()) as anonymous,
    ):
        resp = client.get("/api/ytplaylists/RDTMAK5uy_mix/videos")

    assert resp.status_code == 200
    assert resp.json()["title"] == "Mon supermix"
    assert [v["video_id"] for v in resp.json()["items"]] == ["likedAAAAAA"]
    signed.assert_awaited_once()
    anonymous.assert_not_awaited()


def test_private_playlist_falls_back_to_the_signed_in_session(client, settings):
    _push_cookies(settings)
    liked = [Video(video_id="likedAAAAAA", title="Liked A")]
    with (
        patch.object(ytdlp, "playlist_videos", AsyncMock(side_effect=ytdlp.UpstreamError("does not exist"))),
        patch.object(music, "playlist_videos", AsyncMock(return_value=(liked, "Private"))),
    ):
        resp = client.get("/api/ytplaylists/PLprivate/videos")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Private"


def test_public_playlist_stays_anonymous(client, settings):
    _push_cookies(settings)
    with (
        patch.object(ytdlp, "playlist_videos", AsyncMock(return_value=([], "Public"))),
        patch.object(music, "playlist_videos", AsyncMock()) as signed,
    ):
        resp = client.get("/api/ytplaylists/OLAK5uy_album/videos")
    assert resp.json()["title"] == "Public"
    signed.assert_not_awaited()


def test_playlist_failure_without_cookies_is_502(client):
    with patch.object(ytdlp, "playlist_videos", AsyncMock(side_effect=ytdlp.UpstreamError("nope"))):
        resp = client.get("/api/ytplaylists/LM/videos")
    assert resp.status_code == 502
