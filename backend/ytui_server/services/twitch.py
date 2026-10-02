"""Twitch service: batched live detection via the public GQL API and
ad-free playlist resolution through TTV.LOL-compatible playlist proxies.

No API key needed: the GQL client-id is the public one of Twitch's web
player (the same yt-dlp and streamlink have used for years). One batched
query covers every followed channel, so polling can be fast.

Ad avoidance: Twitch ads are server-side stitched (SSAI) into the HLS
stream, but Twitch does not insert ad breaks for viewers in a handful of
countries. Playlist proxies (luminous, perfprod) fetch the master playlist
through such a country; only the playlist request transits the proxy — the
video segments are then pulled directly from Twitch's CDN. This is a
cat-and-mouse game, so resolution always falls back to a direct (ad-fed)
stream when every proxy fails.

A proxy still lets a preroll through now and then. The ad decision belongs to
the playback token and holds for every variant of it (measured 2026-10-02: a
direct French token got a preroll on all five variants, 6 tokens out of 6),
so probing the lightest variant tells whether a master is clean; a fresh
master from the same proxy carries a fresh token, hence a new draw.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from datetime import datetime

import httpx

from ..models import Video

log = logging.getLogger(__name__)

GQL_URL = "https://gql.twitch.tv/gql"
GQL_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
TIMEOUT = httpx.Timeout(10.0)
_BATCH = 30

DEFAULT_PROXIES = "https://eu.luminous.dev,https://eu2.luminous.dev,https://lb-as.cdn-perfprod.com"

# Stitched ad segments come with an EXT-X-DATERANGE of CLASS
# "twitch-stitched-ad" and ID "stitched-ad-<ts>-<duration>".
_AD_MARKER = "stitched-ad"
# Fresh tokens drawn from one proxy before moving on to the next.
AD_FREE_ATTEMPTS = 2

_LIVE_QUERY = (
    "query($logins:[String!]){users(logins:$logins){login displayName "
    "stream{id title type viewersCount createdAt}}}"
)

LOGIN_RE = re.compile(r"[a-z0-9_]{4,25}")
TWITCH_URL_RE = re.compile(r"(?:www\.)?twitch\.tv/([A-Za-z0-9_]{4,25})")
# First path segments of twitch.tv that are not channel names.
_RESERVED = frozenset(
    {
        "videos",
        "directory",
        "clips",
        "settings",
        "downloads",
        "subscriptions",
        "wallet",
        "turbo",
        "prime",
        "store",
        "popout",
        "embed",
        "moderator",
        "dashboard",
    }
)


class TwitchError(Exception):
    """A Twitch upstream call failed."""


def parse_login(ref: str) -> str | None:
    """Extract and validate a channel login from 'twitch:<login>' or a twitch.tv URL."""
    login: str | None = None
    if ref.startswith("twitch:"):
        login = ref[len("twitch:") :].strip()
    else:
        m = TWITCH_URL_RE.search(ref)
        if m:
            login = m.group(1)
    if login is None:
        return None
    login = login.lower()
    if login in _RESERVED or not LOGIN_RE.fullmatch(login):
        raise ValueError(f"Invalid Twitch channel {login!r} (expected twitch:<login>)")
    return login


def live_thumbnail(login: str) -> str:
    return f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{login}-320x180.jpg"


async def _gql_users(
    client: httpx.AsyncClient, logins: list[str]
) -> list[dict | None]:
    resp = await client.post(
        GQL_URL,
        json=[{"query": _LIVE_QUERY, "variables": {"logins": logins}}],
        headers={"Client-ID": GQL_CLIENT_ID},
    )
    resp.raise_for_status()
    try:
        payload = resp.json()
        return (payload[0].get("data") or {}).get("users") or []
    except (ValueError, IndexError, KeyError, AttributeError, TypeError) as exc:
        raise TwitchError(f"Unexpected GQL response: {exc}") from exc


async def resolve_display_names(
    client: httpx.AsyncClient, logins: list[str]
) -> dict[str, str]:
    """Map login -> display name. Unknown logins are absent from the result."""
    names: dict[str, str] = {}
    for i in range(0, len(logins), _BATCH):
        for user in await _gql_users(client, logins[i : i + _BATCH]):
            if user:
                login = user.get("login") or ""
                if login:
                    names[login.lower()] = user.get("displayName") or login
    return names


async def check_live_batch(
    client: httpx.AsyncClient, logins: list[str]
) -> dict[str, Video]:
    """Return {login: Video} for followed channels that are currently live."""
    live: dict[str, Video] = {}
    for i in range(0, len(logins), _BATCH):
        for user in await _gql_users(client, logins[i : i + _BATCH]):
            if not user:
                continue
            stream = user.get("stream")
            if not stream or stream.get("type") != "live" or not stream.get("id"):
                continue
            login = (user.get("login") or "").lower()
            if not login:
                continue
            display = user.get("displayName") or login
            created = stream.get("createdAt") or ""
            try:
                published = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                published = None
            live[login] = Video(
                video_id=f"{login}:{stream['id']}",
                title=stream.get("title") or f"{display} (live)",
                channel_id=login,
                channel_title=display,
                published=published,
                thumbnail_url=live_thumbnail(login),
                platform="twitch",
            )
    return live


_STREAM_INF_RE = re.compile(r"RESOLUTION=(\d+)x(\d+)")


def parse_variants(master: str) -> list[tuple[int, str]]:
    """[(height, variant playlist URL)] from an HLS master playlist.

    Variants without a RESOLUTION attribute (audio_only) are skipped.
    """
    variants: list[tuple[int, str]] = []
    lines = master.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        m = _STREAM_INF_RE.search(line)
        if not m:
            continue
        uri = next(
            (u.strip() for u in lines[i + 1 :] if u.strip() and not u.startswith("#")), ""
        )
        if uri:
            variants.append((int(m.group(2)), uri))
    return variants


def has_ads(media_playlist: str) -> bool:
    """Whether a Twitch media playlist carries stitched ad segments."""
    return _AD_MARKER in media_playlist


def _is_twitch_cdn(url: str) -> bool:
    """Probes only follow URLs pointing at Twitch's own CDN: the master comes
    from a third-party proxy and must not steer server-side fetches elsewhere."""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and host.endswith(".ttvnw.net")


async def _fetch_master(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        # Redirects stay unfollowed: the proxy client would chase them past
        # the /api/proxy allowlist.
        resp = await client.get(url, follow_redirects=False)
    except httpx.HTTPError as exc:
        log.info("twitch playlist proxy %s unreachable: %s", url, exc)
        return None
    if resp.status_code == 200 and resp.text.startswith("#EXTM3U"):
        return resp.text
    log.info("twitch playlist proxy %s returned HTTP %s", url, resp.status_code)
    return None


async def _carries_preroll(client: httpx.AsyncClient, master: str) -> bool:
    """Probe the lightest variant of a master for stitched ads.

    An inconclusive probe (no variant, foreign host, upstream error) counts as
    clean: it must never cost a working proxy.
    """
    variants = parse_variants(master)
    if not variants:
        return False
    variant = min(variants)[1]
    if not _is_twitch_cdn(variant):
        return False
    try:
        resp = await client.get(variant, follow_redirects=False)
    except httpx.HTTPError:
        return False
    return resp.status_code == 200 and has_ads(resp.text)


async def fetch_ad_free_master(
    client: httpx.AsyncClient, url: str, attempts: int = AD_FREE_ATTEMPTS
) -> tuple[str | None, bool]:
    """(master text, clean) from a playlist proxy URL, re-fetched — fresh
    token, new ad draw — while its playback session opens on a preroll.

    The text is None when the proxy is unusable; clean is False when every
    attempt drew a preroll (the last master is still returned).
    """
    master: str | None = None
    for _ in range(max(1, attempts)):
        master = await _fetch_master(client, url)
        if master is None:
            return None, False
        if not await _carries_preroll(client, master):
            return master, True
        log.info("twitch playlist proxy %s served a preroll", url)
    return master, False


def _pick(url: str, master: str, max_height: int) -> tuple[str, int | None]:
    variants = parse_variants(master)
    top = max((h for h, _ in variants), default=0)
    if variants and top > max_height:
        height, variant = max(
            (v for v in variants if v[0] <= max_height),
            default=min(variants),
        )
        return variant, height
    return url, top or None


async def resolve_live_playlist(
    client: httpx.AsyncClient, login: str, proxies: list[str], max_height: int = 4320
) -> tuple[str, int | None] | None:
    """(URL, height) of an ad-free live playlist via the first healthy
    TTV.LOL-compatible proxy, or None when every proxy fails.

    The proxy URL itself is returned (not its content) so the player fetches a
    fresh master playlist — and fresh segment tokens — when it opens the stream.
    The exception is a cap that excludes variants: then one variant playlist URL
    is handed over instead, since a master carries every quality.

    A proxy whose tokens keep drawing prerolls is skipped for the next one;
    when they all do, the first answering proxy is still preferred over the
    direct stream (the player's own fetch is a new draw).
    """
    fallback: tuple[str, int | None] | None = None
    for base in proxies:
        base = base.strip().rstrip("/")
        if not base:
            continue
        url = f"{base}/live/{login}?allow_source=true&allow_audio_only=true&fast_bread=true"
        master, clean = await fetch_ad_free_master(client, url)
        if master is None:
            continue
        picked = _pick(url, master, max_height)
        if clean:
            return picked
        fallback = fallback or picked
    return fallback
