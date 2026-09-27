"""YouTube Music: the account's music home and its personal playlists.

music.youtube.com is driven by InnerTube's WEB_REMIX client. With the account
cookies pushed for the YouTube home (services/cookies.py) the same session
authenticates there too, and `browse FEmusic_home` returns the shelves the site
shows ("Sélection rapide", "Mixés pour vous", "Albums pour vous"…) in the same
order and in the account's language.

The cookie jar is only ever READ here. The server-side browser owns the session
and re-pushes it every few minutes; rewriting the file from this side is what
destroyed sessions before (see ytdlp._save_cookies).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import httpx

from ..models import MusicShelf, Video
from . import ytdlp
from .cookies import CookieStore
from .ytdlp import UpstreamError

MUSIC_ORIGIN = "https://music.youtube.com"
_BROWSE_URL = f"{MUSIC_ORIGIN}/youtubei/v1/browse?prettyPrint=false"
# The first response carries 3 shelves; measured 5 more pages for 16 shelves.
_HOME_MAX_PAGES = 8
# Playlist pages hold 100 tracks each.
_PLAYLIST_MAX_PAGES = 10

NO_COOKIES_WARNING = (
    "YouTube Music needs the account cookies: run `ytui auth cookies` on the desktop"
)
STALE_COOKIES_WARNING = (
    "YouTube rejected the account cookies: run `ytui auth cookies` again"
)

log = logging.getLogger(__name__)


# ─── parsing ───


def _text(node: object) -> str:
    """Concatenated runs of an InnerTube text node."""
    if not isinstance(node, dict):
        return ""
    return "".join(run.get("text", "") for run in node.get("runs") or [])


_COUNT_RE = re.compile(
    r"^(\d[\d ]*(?:[.,]\d+)?)\s*(mrd|md|k|m|b)?\s*(?:de\s+)?"
    r"(?:vues?|lectures?|views?|plays?)$",
    re.IGNORECASE,
)
_COUNT_MULTIPLIERS = {"": 1, "k": 1_000, "m": 1_000_000, "md": 10**9, "mrd": 10**9, "b": 10**9}


def _parse_count(text: str) -> int | None:
    """'222 M lectures' / '2,7 Md de vues' / '1.2M views' -> int, else None."""
    normalized = re.sub(r"[\xa0\u202f]", " ", text).strip()
    match = _COUNT_RE.match(normalized)
    if not match:
        return None
    number = float(match.group(1).replace(" ", "").replace(",", "."))
    return int(number * _COUNT_MULTIPLIERS[(match.group(2) or "").lower()])


@dataclass
class _Subtitle:
    """A subtitle line split into what the Video model can hold."""

    channel_title: str = ""
    channel_id: str = ""
    view_count: int | None = None
    duration: int | None = None
    parts: list[str] = field(default_factory=list)


def _subtitle(node: object) -> _Subtitle:
    """'Linkin Park • 479 M lectures' → channel 'Linkin Park' + 479M views.

    Counts and durations become fields; what remains ("Album • Linkin Park",
    "Stormzy, Michael Jackson et Skillet") is shown as the channel line, like
    the site's second row.
    """
    out = _Subtitle()
    runs = node.get("runs") or [] if isinstance(node, dict) else []
    for run in runs:
        browse = (run.get("navigationEndpoint") or {}).get("browseEndpoint") or {}
        browse_id = browse.get("browseId") or ""
        if browse_id.startswith("UC") and not out.channel_id:
            out.channel_id = browse_id
    for part in re.split(r"\s*•\s*", _text(node)):
        part = part.strip()
        if not part:
            continue
        count = _parse_count(part)
        if count is not None:
            out.view_count = count
            continue
        duration = ytdlp._parse_badge_duration(part)
        if duration is not None:
            out.duration = duration
            continue
        out.parts.append(part)
    out.channel_title = " • ".join(out.parts)
    return out


_GOOGLEUSERCONTENT_SIZE_RE = re.compile(r"=w\d+-h\d+")


def _thumbnail(renderer: dict) -> str:
    """Largest thumbnail; square cover art is re-requested at 544 px."""
    holder = renderer.get("thumbnailRenderer") or renderer.get("thumbnail") or {}
    thumbs: list = []
    for found in ytdlp._walk_key(holder, "thumbnail"):
        thumbs = found.get("thumbnails") or []
        if thumbs:
            break
    if not thumbs:
        return ""
    url = max(thumbs, key=lambda t: t.get("width") or 0).get("url") or ""
    if "googleusercontent.com" in url:
        url = _GOOGLEUSERCONTENT_SIZE_RE.sub("=w544-h544", url)
    return url


def _page_type(browse: dict) -> str:
    return (
        (browse.get("browseEndpointContextSupportedConfigs") or {})
        .get("browseEndpointContextMusicConfig", {})
        .get("pageType", "")
    )


def _play_playlist_id(renderer: dict) -> str:
    """Playlist an album/playlist card plays (OLAK5uy_… for albums)."""
    for scope in (renderer.get("thumbnailOverlay"), renderer.get("menu")):
        for endpoint in ytdlp._walk_key(scope, "watchPlaylistEndpoint"):
            if endpoint.get("playlistId"):
                return endpoint["playlistId"]
    return ""


def _target(renderer: dict) -> tuple[str, str] | None:
    """(kind, id) the card opens: ('video', id), ('playlist', id) or ('channel', UC…)."""
    item_video = (renderer.get("playlistItemData") or {}).get("videoId")
    if item_video:
        return "video", item_video
    nav = renderer.get("navigationEndpoint") or {}
    watch = nav.get("watchEndpoint") or {}
    if watch.get("videoId"):
        return "video", watch["videoId"]
    browse = nav.get("browseEndpoint")
    if isinstance(browse, dict):
        browse_id = browse.get("browseId") or ""
        page = _page_type(browse)
        if page == "MUSIC_PAGE_TYPE_ALBUM":
            # MPREb_ album ids only exist on YouTube Music; the audio
            # playlist behind them is an ordinary YouTube playlist.
            playlist_id = _play_playlist_id(renderer)
            return ("playlist", playlist_id) if playlist_id else None
        if page == "MUSIC_PAGE_TYPE_PLAYLIST":
            playlist_id = browse_id[2:] if browse_id.startswith("VL") else browse_id
            return ("playlist", playlist_id) if playlist_id else None
        if browse_id.startswith("UC"):
            return "channel", browse_id
        return None  # podcasts, moods, library pages…
    watch_playlist = nav.get("watchPlaylistEndpoint") or {}
    if watch_playlist.get("playlistId"):
        return "playlist", watch_playlist["playlistId"]
    # List rows without a row-level endpoint: the title or play button carries it.
    for scope in (renderer.get("flexColumns"), renderer.get("overlay")):
        for endpoint in ytdlp._walk_key(scope, "watchEndpoint"):
            if endpoint.get("videoId"):
                return "video", endpoint["videoId"]
    return None


def _item_to_video(item: dict) -> Video | None:
    """A shelf/playlist entry (two-row card or list row) as a Video."""
    if "musicTwoRowItemRenderer" in item:
        renderer = item["musicTwoRowItemRenderer"]
        title = _text(renderer.get("title"))
        subtitle = _subtitle(renderer.get("subtitle"))
    elif "musicResponsiveListItemRenderer" in item:
        renderer = item["musicResponsiveListItemRenderer"]
        columns = [
            (col.get("musicResponsiveListItemFlexColumnRenderer") or {}).get("text")
            for col in renderer.get("flexColumns") or []
        ]
        title = _text(columns[0]) if columns else ""
        subtitle = _subtitle(columns[1] if len(columns) > 1 else None)
        for col in renderer.get("fixedColumns") or []:
            fixed = _text((col.get("musicResponsiveListItemFixedColumnRenderer") or {}).get("text"))
            duration = ytdlp._parse_badge_duration(fixed) if fixed else None
            if duration is not None:
                subtitle.duration = duration
    else:
        return None
    target = _target(renderer)
    if target is None or not title:
        return None
    kind, target_id = target
    if kind == "channel":
        return Video(
            video_id=target_id,
            title=title,
            channel_title=title,
            channel_id=target_id,
            thumbnail_url=_thumbnail(renderer),
            kind="channel",
        )
    return Video(
        video_id=target_id,
        title=title,
        channel_title=subtitle.channel_title,
        channel_id=subtitle.channel_id,
        thumbnail_url=_thumbnail(renderer),
        duration=subtitle.duration,
        view_count=subtitle.view_count,
        kind=kind,  # type: ignore[arg-type]
    )


_SHELF_KEYS = ("musicCarouselShelfRenderer", "musicImmersiveCarouselShelfRenderer")


def _parse_shelves(data: dict) -> list[MusicShelf]:
    """Every carousel shelf in a home page, in page order."""
    shelves: list[MusicShelf] = []
    for section in ytdlp._walk_key(data, "sectionListRenderer") + ytdlp._walk_key(
        data, "sectionListContinuation"
    ):
        for entry in section.get("contents") or []:
            shelf = next((entry[k] for k in _SHELF_KEYS if k in entry), None)
            if shelf is None:
                continue
            header = next(iter((shelf.get("header") or {}).values()), {}) or {}
            items: list[Video] = []
            seen: set[tuple[str, str]] = set()
            for item in shelf.get("contents") or []:
                try:
                    video = _item_to_video(item)
                except Exception:  # one odd card must not cost the shelf
                    log.debug("music: unparsable shelf item", exc_info=True)
                    continue
                if video is None or (video.kind, video.video_id) in seen:
                    continue
                seen.add((video.kind, video.video_id))
                items.append(video)
            title = _text(header.get("title"))
            if items and title:
                shelves.append(
                    MusicShelf(title=title, strapline=_text(header.get("strapline")), items=items)
                )
    return shelves


def _home_continuation(data: dict) -> str:
    """Token of the next batch of home shelves, or ''."""
    for section in ytdlp._walk_key(data, "sectionListRenderer") + ytdlp._walk_key(
        data, "sectionListContinuation"
    ):
        for cont in section.get("continuations") or []:
            token = (cont.get("nextContinuationData") or {}).get("continuation")
            if token:
                return token
    return ""


def _playlist_title(data: dict) -> str:
    for key in ("musicResponsiveHeaderRenderer", "musicDetailHeaderRenderer"):
        for header in ytdlp._walk_key(data, key):
            title = _text(header.get("title"))
            if title:
                return title
    return ""


def _playlist_items(data: dict) -> tuple[list[dict], str]:
    """Rows of a playlist page (first page or continuation) + the next token."""
    rows: list[dict] = []
    for shelf in ytdlp._walk_key(data, "musicPlaylistShelfRenderer"):
        rows += shelf.get("contents") or []
    for action in data.get("onResponseReceivedActions") or []:
        rows += (action.get("appendContinuationItemsAction") or {}).get("continuationItems") or []
    token = ""
    for renderer in ytdlp._walk_key(rows, "continuationItemRenderer"):
        command = (renderer.get("continuationEndpoint") or {}).get("continuationCommand") or {}
        token = command.get("token") or token
    return rows, token


# ─── InnerTube session ───


def _sapisid_hash(jar) -> str | None:
    values = {c.name: c.value for c in jar}
    sapisid = values.get("SAPISID") or values.get("__Secure-3PAPISID")
    if not sapisid:
        return None
    now = int(time.time())
    digest = hashlib.sha1(f"{now} {sapisid} {MUSIC_ORIGIN}".encode()).hexdigest()
    return f"SAPISIDHASH {now}_{digest}"


class _Session:
    """A signed-in WEB_REMIX InnerTube client."""

    def __init__(self, client: httpx.AsyncClient, jar, context: dict) -> None:
        self._client = client
        self._jar = jar
        self._context = context

    async def browse(self, body: dict, *, params: dict | None = None) -> dict:
        headers = {
            "Content-Type": "application/json",
            "X-Youtube-Client-Name": "67",  # WEB_REMIX
            "X-Youtube-Client-Version": self._context.get("client", {}).get("clientVersion", ""),
            "X-Origin": MUSIC_ORIGIN,
            "Origin": MUSIC_ORIGIN,
            "Referer": f"{MUSIC_ORIGIN}/",
            "X-Goog-AuthUser": "0",
        }
        authorization = _sapisid_hash(self._jar)
        if authorization:
            headers["Authorization"] = authorization
        resp = await self._client.post(
            _BROWSE_URL, params=params, json={"context": self._context, **body}, headers=headers
        )
        if resp.status_code == 429:
            raise UpstreamError("YouTube Music rate-limited the request (429)")
        resp.raise_for_status()
        return resp.json()


@asynccontextmanager
async def _session(cookie_file: Path) -> AsyncIterator[_Session | None]:
    """A signed-in session, or None when the cookies no longer authenticate."""
    from yt_dlp.cookies import YoutubeDLCookieJar

    jar = YoutubeDLCookieJar(str(cookie_file))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        raise UpstreamError(f"unreadable cookie file: {exc}") from exc
    async with httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=True,
        cookies=jar,
        headers={"User-Agent": ytdlp._BROWSER_UA},
    ) as client:
        try:
            resp = await client.get(f"{MUSIC_ORIGIN}/")
            if resp.status_code == 429:
                raise UpstreamError("YouTube Music rate-limited the request (429)")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"music.youtube.com: {exc}") from exc
        html = resp.text
        # A dead session is not an error: the site just serves the anonymous
        # home, which must not be presented as the account's.
        if '"LOGGED_IN":true' not in html:
            log.warning("YouTube Music served anonymously: the cookies no longer authenticate")
            yield None
            return
        try:
            context = ytdlp._json_object_after(html, '"INNERTUBE_CONTEXT":')
        except (ValueError, KeyError) as exc:
            raise UpstreamError("INNERTUBE_CONTEXT not found on music.youtube.com") from exc
        yield _Session(client, jar, context)


async def home(cookie_file: Path, max_pages: int = _HOME_MAX_PAGES) -> list[MusicShelf] | None:
    """The account's YouTube Music home shelves; None when signed out."""
    try:
        async with _session(cookie_file) as session:
            if session is None:
                return None
            data = await session.browse({"browseId": "FEmusic_home"})
            shelves = _parse_shelves(data)
            token = _home_continuation(data)
            for _ in range(max_pages):
                if not token:
                    break
                try:
                    data = await session.browse(
                        {}, params={"ctoken": token, "continuation": token, "type": "next"}
                    )
                except (UpstreamError, httpx.HTTPError, ValueError) as exc:
                    # The shelves already fetched are a usable home.
                    log.warning("music home pagination stopped: %s", exc)
                    break
                shelves += _parse_shelves(data)
                token = _home_continuation(data)
    except UpstreamError:
        raise
    except Exception as exc:
        log.warning("music home failed: %s", exc)
        raise UpstreamError(str(exc)) from exc
    return shelves


def is_personal_playlist(playlist_id: str) -> bool:
    """Playlists only the signed-in account can list correctly.

    "LM" (liked music) does not exist anonymously; RDTMAK… mixes ("Mon
    supermix", "Mon Mix 1"…) do, but anonymously they list generic tracks
    instead of the account's.
    """
    return playlist_id == "LM" or playlist_id.startswith("RDTMAK")


async def playlist_videos(
    cookie_file: Path, playlist_id: str, limit: int = 200
) -> tuple[list[Video], str] | None:
    """A playlist listed through the signed-in session; None when signed out."""
    videos: list[Video] = []
    seen: set[str] = set()
    try:
        async with _session(cookie_file) as session:
            if session is None:
                return None
            data = await session.browse({"browseId": f"VL{playlist_id}"})
            title = _playlist_title(data)
            for page in range(_PLAYLIST_MAX_PAGES):
                rows, token = _playlist_items(data)
                for row in rows:
                    video = _item_to_video(row)
                    if video is None or video.kind != "video" or video.video_id in seen:
                        continue
                    seen.add(video.video_id)
                    videos.append(video)
                if len(videos) >= limit or not token or page == _PLAYLIST_MAX_PAGES - 1:
                    break
                data = await session.browse({"continuation": token})
    except UpstreamError:
        raise
    except Exception as exc:
        log.warning("music playlist %s failed: %s", playlist_id, exc)
        raise UpstreamError(str(exc)) from exc
    return videos[:limit], title


# ─── service ───


@dataclass
class MusicHome:
    shelves: list[MusicShelf]
    warnings: list[str] = field(default_factory=list)


class MusicService:
    """Cached YouTube Music home (in memory: cheap to rebuild, personal, short-lived)."""

    def __init__(self, cookies: CookieStore | None, ttl_seconds: int = 30 * 60) -> None:
        self.cookies = cookies
        self.ttl_seconds = ttl_seconds
        self._cached: list[MusicShelf] | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    def _fresh(self) -> bool:
        return (
            self._cached is not None
            and time.monotonic() - self._fetched_at < self.ttl_seconds
        )

    async def home(self, force_refresh: bool = False) -> MusicHome:
        if self.cookies is None or not self.cookies.exists():
            return MusicHome([], [NO_COOKIES_WARNING])
        if not force_refresh and self._fresh():
            return MusicHome(list(self._cached or []))
        async with self._lock:
            # A concurrent caller may have refreshed while we waited.
            if not force_refresh and self._fresh():
                return MusicHome(list(self._cached or []))
            try:
                shelves = await home(self.cookies.path)
            except UpstreamError as exc:
                log.warning("music home failed: %s", exc)
                return MusicHome(list(self._cached or []), [f"YouTube Music: {exc}"])
            if shelves is None:
                return MusicHome([], [STALE_COOKIES_WARNING])
            self._cached = shelves
            self._fetched_at = time.monotonic()
            return MusicHome(list(shelves))

    async def _signed_in_playlist(
        self, playlist_id: str, limit: int
    ) -> tuple[list[Video], str] | None:
        """Signed-in listing, or None (no/dead cookies, failure, empty)."""
        if self.cookies is None or not self.cookies.exists():
            return None
        try:
            listed = await playlist_videos(self.cookies.path, playlist_id, limit=limit)
        except UpstreamError as exc:
            log.warning("signed-in listing of %s failed: %s", playlist_id, exc)
            return None
        return listed if listed and listed[0] else None

    async def youtube_playlist(
        self, playlist_id: str, limit: int = 200
    ) -> tuple[list[Video], str]:
        """A YouTube playlist: anonymous yt-dlp listing, signed-in when it must be.

        Personal mixes go through the account first (anonymously they list
        generic tracks); anything yt-dlp cannot list (liked music, private
        playlists) gets a signed-in second chance. Raises UpstreamError.
        """
        if is_personal_playlist(playlist_id):
            listed = await self._signed_in_playlist(playlist_id, limit)
            if listed is not None:
                return listed
        url = Video(video_id=playlist_id, title="", kind="playlist").url
        try:
            return await ytdlp.playlist_videos(url, limit=limit)
        except UpstreamError:
            listed = await self._signed_in_playlist(playlist_id, limit)
            if listed is not None:
                return listed
            raise
