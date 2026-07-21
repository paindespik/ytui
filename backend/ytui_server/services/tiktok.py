"""TikTok service: live detection and stream resolution for followed accounts.

Lives only — no video feed, no search. Two unsigned, login-free endpoints:
- api-live/user/room maps a username to its room id and liveness
  (liveRoom.status == 2 means live, 4 means offline).
- webcast/room/info exposes the playable stream URLs for a live room.

Stream resolution stays on httpx because yt-dlp's TikTokLiveIE wrongly
reports FLV-only rooms (e.g. game streams with an empty hls_pull_url) as
"not currently live" — its /api/live/detail fallback is a dead endpoint.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import httpx

from ..models import Video

log = logging.getLogger(__name__)

USER_ROOM_URL = "https://www.tiktok.com/api-live/user/room/"
ROOM_INFO_URL = "https://webcast.tiktok.com/webcast/room/info/"
TIMEOUT = httpx.Timeout(10.0)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ytui-server/0.2"

USERNAME_RE = re.compile(r"[A-Za-z0-9_.]{1,24}")
TIKTOK_URL_RE = re.compile(r"(?:www\.)?tiktok\.com/@([A-Za-z0-9_.]{1,24})")

# TikTok's "user_not_found" statusCode on api-live/user/room (HTTP 200).
_USER_NOT_FOUND = 19881007
_LIVE_STATUS = 2
_FLV_PREFERENCE = ("FULL_HD1", "HD1", "SD2", "SD1")
# Quality keys inside live_core_sdk_data (best first); "ao" is audio-only, last.
_SDK_QUALITY_PREFERENCE = ("origin", "uhd", "hd", "sd", "ld")


class TikTokError(Exception):
    """A TikTok upstream call returned an unusable payload."""


def parse_username(ref: str) -> str | None:
    """Extract and validate a username from 'tiktok:<user>' or a tiktok.com URL.

    Bare names never resolve to TikTok (they belong to the YouTube/Twitch
    fallback chain in resolve_ref).
    """
    username: str | None = None
    if ref.startswith("tiktok:"):
        username = ref[len("tiktok:") :].strip().removeprefix("@")
    else:
        m = TIKTOK_URL_RE.search(ref)
        if m:
            username = m.group(1)
    if username is None:
        return None
    username = username.lower()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError(f"Invalid TikTok username {username!r} (expected tiktok:<user>)")
    return username


def live_url(username: str) -> str:
    return f"https://www.tiktok.com/@{username}/live"


async def fetch_user_room(client: httpx.AsyncClient, username: str) -> dict:
    """Return the raw user/room payload ({"user": …, "liveRoom": …, …}).

    Raises TikTokError for unknown users and unexpected payloads; transient
    httpx errors propagate untouched (callers decide how to degrade).
    """
    resp = await client.get(
        USER_ROOM_URL,
        params={"aid": "1988", "sourceType": "54", "uniqueId": username},
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    try:
        payload = resp.json()
    except ValueError as exc:
        raise TikTokError(f"Unexpected TikTok response: {exc}") from exc
    if not isinstance(payload, dict):
        raise TikTokError("Unexpected TikTok response")
    if (
        payload.get("statusCode") == _USER_NOT_FOUND
        or payload.get("message") == "user_not_found"
    ):
        raise TikTokError(f"Unknown TikTok user {username!r}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise TikTokError("Unexpected TikTok response")
    return data


async def check_live(
    client: httpx.AsyncClient, username: str, channel_title: str = ""
) -> Video | None:
    """Return the live Video for a username, or None when offline."""
    data = await fetch_user_room(client, username)
    user = data.get("user") or {}
    live_room = data.get("liveRoom") or {}
    room_id = str(user.get("roomId") or "")
    if live_room.get("status") != _LIVE_STATUS or not room_id:
        return None
    nickname = user.get("nickname") or ""
    published = None
    start_time = live_room.get("startTime") or 0
    try:
        if start_time:
            published = datetime.fromtimestamp(start_time, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        published = None
    return Video(
        # room_id changes on every stream, so a new live re-notifies.
        video_id=f"{username}:{room_id}",
        title=live_room.get("title") or f"{nickname or username} (live)",
        channel_id=username,
        channel_title=nickname or channel_title or username,
        published=published,
        thumbnail_url=live_room.get("coverUrl") or "",
        platform="tiktok",
    )


def _sdk_streams(stream_url: dict) -> dict[str, dict]:
    """Parse TikTok's newer live_core_sdk_data.pull_data.stream_data blob.

    Returns {quality: {"hls": url, "flv": url, "cmaf": url}}. TikTok now leaves
    the flat hls_/flv_pull_url fields empty for many rooms and only populates
    this JSON-encoded blob.
    """
    sdk = stream_url.get("live_core_sdk_data")
    if not isinstance(sdk, dict):
        return {}
    raw = (sdk.get("pull_data") or {}).get("stream_data")
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        data = json.loads(raw).get("data")
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for quality, entry in data.items():
        main = (entry or {}).get("main") or {}
        out[quality] = {proto: main.get(proto) or "" for proto in ("hls", "flv", "cmaf")}
    return out


def _sdk_ordered(sdk: dict[str, dict]) -> list[dict]:
    """SDK quality entries, best first, with audio-only ('ao') last."""
    ordered = [sdk[q] for q in _SDK_QUALITY_PREFERENCE if q in sdk]
    ordered += [v for q, v in sdk.items() if q not in _SDK_QUALITY_PREFERENCE and q != "ao"]
    if "ao" in sdk:
        ordered.append(sdk["ao"])
    return ordered


def _pick_stream_url(stream_url: dict) -> str:
    """Best playable URL for mpv / mpegts.js / hls.js.

    FLV is preferred: TikTok's HLS pull URLs (both the flat fields and the SDK
    blob) frequently answer 403 while the FLV endpoints stream fine, so HLS and
    CMAF are kept only as last-resort fallbacks.
    """
    flv_map = stream_url.get("flv_pull_url")
    sdk = _sdk_streams(stream_url)

    # 1. FLV — the reliable path (flat by quality, then the SDK blob).
    if isinstance(flv_map, dict):
        for quality in _FLV_PREFERENCE:
            if flv_map.get(quality):
                return flv_map[quality]
        for candidate in flv_map.values():
            if candidate:
                return candidate
    for entry in _sdk_ordered(sdk):
        if entry.get("flv"):
            return entry["flv"]

    # 2. HLS fallback (flat URL, quality map, then the SDK blob).
    if stream_url.get("hls_pull_url"):
        return stream_url["hls_pull_url"]
    hls_map = stream_url.get("hls_pull_url_map")
    if isinstance(hls_map, dict):
        for candidate in hls_map.values():
            if candidate:
                return candidate
    for entry in _sdk_ordered(sdk):
        if entry.get("hls"):
            return entry["hls"]

    # 3. CMAF — last resort.
    for entry in _sdk_ordered(sdk):
        if entry.get("cmaf"):
            return entry["cmaf"]
    return ""


async def resolve_live_stream(
    client: httpx.AsyncClient, username: str
) -> tuple[str, str] | None:
    """Return (stream_url, title) for a live username, or None when offline.

    The username is the only stable part of the live video_id, so the room id
    is re-fetched — it also guards against replaying a stale room.
    """
    data = await fetch_user_room(client, username)
    room_id = str((data.get("user") or {}).get("roomId") or "")
    if not room_id:
        return None
    resp = await client.get(
        ROOM_INFO_URL,
        params={"aid": "1988", "room_id": room_id},
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    try:
        payload = resp.json()
    except ValueError as exc:
        raise TikTokError(f"Unexpected TikTok response: {exc}") from exc
    room = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(room, dict):
        raise TikTokError("Unexpected TikTok response")
    if room.get("status") != _LIVE_STATUS:
        return None
    url = _pick_stream_url(room.get("stream_url") or {})
    if not url:
        return None
    return url, room.get("title") or ""
