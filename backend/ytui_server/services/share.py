"""Public share links: one video watchable without the API token.

A share is a random token bound to one (platform, video_id) in the database.
Its public routes (routers/share_public.py) replay the web player's pipeline
— stream resolution, DASH manifest, same-origin byte/HLS relay — but every
upstream URL they hand out is signed for that token: the relay refuses
anything else, so a link can never be turned into a relay towards other
videos of the allowlisted CDNs.

Signatures are HMAC-SHA256 over (token, expiry, url) with a key derived from
the API token; they expire after SIGNATURE_TTL (the player re-resolves, as it
does when upstream URLs expire). Revoking or expiring the share kills every
signed URL at once: each relay request re-checks the share row.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse

import httpx

from ..models import Video
from . import tiktok, twitch

#: Lifetime of one signed relay URL. Playlists are re-signed on every fetch;
#: a DASH manifest or progressive URL outliving it makes the player re-resolve.
SIGNATURE_TTL = 12 * 3600

_KEY_CONTEXT = b"ytui-share-v1"


class LiveCheckError(Exception):
    """The platform could not tell whether the live is still on."""


def _key(api_token: str) -> bytes:
    return hmac.new(api_token.encode(), _KEY_CONTEXT, hashlib.sha256).digest()


def sign(api_token: str, token: str, url: str, expires: int) -> str:
    message = f"{token}\n{expires}\n{url}".encode()
    digest = hmac.new(_key(api_token), message, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def verify(
    api_token: str,
    token: str,
    url: str,
    expires: int,
    signature: str,
    now: float | None = None,
) -> bool:
    if expires < (time.time() if now is None else now):
        return False
    expected = sign(api_token, token, url, expires)
    return hmac.compare_digest(expected.encode(), signature.encode())


def signed_url(
    api_token: str, token: str, url: str, playlist: bool, now: float | None = None
) -> str:
    """Share-scoped relay URL of one upstream resource (m3u8 or bytes)."""
    expires = int(time.time() if now is None else now) + SIGNATURE_TTL
    query = urllib.parse.urlencode(
        {"u": url, "e": expires, "s": sign(api_token, token, url, expires)},
        quote_via=urllib.parse.quote,
    )
    endpoint = "hls" if playlist else "p"
    return f"/s/{token}/{endpoint}?{query}"


def is_channel_live(platform: str, video_id: str) -> bool:
    """Twitch/TikTok live ids ("login:stream_id"): resolved by login, so the
    same URL would play whatever the channel streams next."""
    return platform in ("twitch", "tiktok") and ":" in video_id


async def current_live(platform: str, login: str) -> Video | None:
    """The channel's live right now (Twitch, TikTok), or None when offline."""
    try:
        if platform == "twitch":
            async with httpx.AsyncClient(timeout=twitch.TIMEOUT) as client:
                lives = await twitch.check_live_batch(client, [login.lower()])
            return lives.get(login.lower())
        async with httpx.AsyncClient(timeout=tiktok.TIMEOUT) as client:
            return await tiktok.check_live(client, login)
    except (httpx.HTTPError, twitch.TwitchError, tiktok.TikTokError) as exc:
        raise LiveCheckError(str(exc)) from exc
